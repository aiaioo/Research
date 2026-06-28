# AI/ML Research Tracker

A pipeline that collects, categorises, and analyses AI/ML papers from conferences, journals, and curated feeds — then identifies the most impactful researchers and institutions contributing to the field.

See [USAGE.md](USAGE.md) for full documentation on all scripts and how to run them.

---

## Top Researchers

Researcher impact is measured by Google Scholar citation count.  Profiles are
collected automatically via `scholar_lookup.py` for the authors who appear most
frequently across the tracked paper corpus.

As of June 2026, **696 researchers** have verified Scholar profiles in the
dataset, of whom **419 have more than 10,000 citations** and **67 have more
than 100,000 citations**.

### Most-cited researchers (June 2026)

| Researcher | Citations | Papers tracked | Affiliation |
|---|---:|---:|---|
| Yoshua Bengio | 1,119,944 | 5 | University of Montreal / Mila |
| Yann LeCun | 479,047 | 6 | Courant Institute / Meta |
| Michael Jordan | 369,208 | 4 | UC Berkeley |
| Aaron Courville | 362,130 | 8 | Université de Montréal / Mila |
| Trevor Darrell | 338,191 | 8 | UC Berkeley |
| Bernhard Schölkopf | 287,530 | 9 | MPI for Intelligent Systems |
| Sergey Levine | 255,932 | 17 | UC Berkeley / Physical Intelligence |
| Yarin Gal | 70,815 | 21 | University of Oxford |
| Neel Nanda | 16,801 | 19 | Google DeepMind |

Full ranked list: [people/RESEARCHERS_FREQUENCY_202606.tsv](people/RESEARCHERS_FREQUENCY_202606.tsv)

> **Note on citation counts.** Citations are sourced from Google Scholar and
> reflect total lifetime citations across all of a researcher's work, not just
> the papers tracked here.  Researchers with very common names (e.g. "Wei Zhang",
> "Kai Chen") may have Scholar profiles merged across multiple individuals, which
> can inflate their counts.

---

## Top Institutions

Institution impact is calculated by summing the Scholar citation counts of all
tracked researchers affiliated with that institution.  Affiliations are extracted
and normalised from Scholar profile pages by `institution_lookup.py`.

As of June 2026, **435 distinct institutions** appear in the dataset, of which
**142 have a combined citation count above 50,000** and **66 above 100,000**.

### Highest-impact institutions (June 2026)

| Institution | Combined citations |
|---|---:|
| University of Montreal / Mila | 1,119,944 |
| UC Berkeley | 979,954 |
| Stanford University | 970,624 |
| MIT | 549,474 |
| Google DeepMind | 476,554 |
| Tsinghua University | 475,215 |
| New York University | 426,340 |
| National University of Singapore | 382,660 |
| University of Oxford | 319,417 |
| University of Illinois | 285,085 |
| Physical Intelligence | 282,262 |

Full ranked list: [people/INSTITUTIONS_FREQUENCY_202606.tsv](people/INSTITUTIONS_FREQUENCY_202606.tsv)

> **Note on institution grouping.** Affiliation strings are taken verbatim from
> Scholar profiles.  Variants of the same institution (e.g. "UC Berkeley" and
> "University of California, Berkeley") are counted separately.  The totals above
> therefore represent a lower bound on the true combined impact of any given
> institution.

---

## Paper corpus

As of June 2026 the tracker holds **40,746 papers** across all sources.
Categorised papers break down as follows:

| Category | Papers |
|---|---:|
| Vision | 9,153 |
| Models | 3,903 |
| Training | 2,895 |
| Voice | 1,727 |
| Safety | 1,454 |
| Memory | 842 |
| Uncategorised | 20,772 |

Categories are assigned automatically by `paper_categorizer.py` using
keyword scoring over titles and abstracts.  The large uncategorised pool
consists mainly of papers from conference proceedings that span multiple
topics or fall outside the six tracked categories.

---

## Scripts and data flow

```
Daily
  paper_searcher.py   →  paper_deduplicator.py  →  paper_categorizer.py

Monthly
  paper_authors.py  →  scholar_lookup.py  →  institution_lookup.py

Anytime
  paper_viewer.py
```

See [USAGE.md](USAGE.md) for full usage details.
