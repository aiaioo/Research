---
name: map-research
description: This skill should be used when the user asks to "map a research area", "find sources for a topic", "track papers on a subject", "build a reading list", or wants to discover where the best work in a field is published. The research topic is passed as an argument (e.g. /map-research quantum computing). Produces a TRACKED_SOURCES.md file covering curated feeds, research groups, and industry labs relevant to that topic.
version: 1.0.0
---

# map-research

Generates a `TRACKED_SOURCES.md` file that maps the landscape of high-quality sources for a given research topic — curated paper feeds, university research groups, and industry labs.

The topic is provided as an argument when the skill is invoked. Everything in this skill is topic-agnostic; apply it to any academic or scientific domain (e.g. quantum computing, structural biology, climate modeling, robotics, economics).

## Input

**`<topic>`** — the research domain passed as an argument (e.g. `/map-research protein folding`). If no argument is given, ask the user to specify one before proceeding.

## Output File

Create `TRACKED_SOURCES.md` in the current working directory (or a path the user specifies). Use the section structure below, populating it with sources relevant to `<topic>`. Not every section will have entries for every topic — omit empty sections rather than leaving them sparse.

Name the file after the topic if the user is likely to create multiple such files (e.g. `TRACKED_SOURCES_quantum_computing.md`).

## Section Structure

### 1. Daily Curated Feeds
Sites or newsletters updated every day with hand-picked or community-upvoted content for `<topic>`. Prefer sources where a human or tight community makes the selection over pure algorithms.

Table columns: `Site | URL | Notes`

### 2. Weekly Curated Feeds
Newsletters, GitHub repos, or blogs updated on a weekly cadence. Focus on sources with editorial judgment — named authors, clear curation criteria.

Table columns: `Site | URL | Notes`

### 3. Bi-weekly / Monthly / Irregular
High-quality but infrequent sources: long-form journals, curated reading lists, workshop proceedings digests, annual reviews.

Table columns: `Site | URL | Notes`

### 4. Social Feeds Worth Following
Named individuals or communities (X/Twitter accounts, subreddits, Discord servers, Mastodon instances) known to surface top `<topic>` work early. Use a bullet list, not a table.

### 5. University Research Groups
The most impactful academic labs working on `<topic>`. Organize by region (North America / Europe / Asia & Rest of World). For each group note the PI(s), institution, lab URL, and specific focus within the topic. Best followed via the lab's publications page and the PI's social feed.

Table columns: `Lab | PI(s) | University | URL | Focus`

Verify PI affiliations — researchers move between academia and industry frequently. Correct any common misconceptions and note dual affiliations where relevant.

### 6. Industry-Affiliated Research Labs
Labs funded by or embedded within companies but publishing peer-reviewed work on `<topic>`. Organize by geography (United States / Independent & Non-profit / Europe / Asia). Include a research blog URL column where one exists — that is often the best place to catch paper releases.

Table columns: `Lab | Parent | URL | Research Blog | Focus`

### 7. Algorithmic / Uncurated Feeds
High-volume or automated sources relevant to `<topic>` excluded from the curated sections above. Useful for breadth but require more filtering. Include update cadence.

Table columns: `Site | URL | Update Cadence | Notes`

## Quality Bar

- **Curated feeds**: prefer sources where a named person or tight community makes the selection. Move pure-algorithmic ranking to section 7.
- **Research groups**: include only groups with a demonstrated record of top-venue publications or outsized real-world impact in `<topic>`. Do not pad with mid-tier groups.
- **Industry labs**: include labs that publish openly. Exclude labs whose output is primarily proprietary or product-facing with no research publication record.
- **Relevance**: every entry must be specifically relevant to `<topic>`, not just generally about science or technology.

## Tone and Format

- Use tables for structured entries; bullet lists for social/informal sources.
- Notes column: one concise clause — what makes this source distinctive.
- No marketing language. Be direct about limitations (e.g. "quality varies", "requires filtering").
- Add a short intro line under each `##` header to orient a reader coming in cold.

## Example Invocations

`/map-research quantum computing`
→ Covers feeds like IEEE Spectrum Quantum, Quanta Magazine; university groups (Preskill at Caltech, Martinis at UCSB, Lukin at Harvard); industry labs (Google Quantum AI, IBM Research Quantum, Microsoft Azure Quantum).

`/map-research structural biology`
→ Covers feeds like bioRxiv new submissions, RCSB PDB newsletter; university groups (Baker Lab at UW, Bharat Lab at EMBL); industry labs (DeepMind for AlphaFold, Schrödinger Research).

`/map-research climate modeling`
→ Covers feeds like AGU journals, Carbon Brief; university groups (GFDL at Princeton, NCAR affiliates); industry labs (Google Research Climate, NVIDIA Earth-2 project).
