# discogser

[![CI](https://github.com/JDStraughan/discogser/actions/workflows/ci.yml/badge.svg)](https://github.com/JDStraughan/discogser/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Unlicense](https://img.shields.io/badge/license-Unlicense-blue.svg)](LICENSE)

Catalog your vinyl into [Discogs](https://www.discogs.com) from phone photos.

You photograph each album as **exactly 3 shots, in strict order**, then repeat:

1. **front cover**
2. **back cover**
3. **side A runout** — a macro shot of the etched/stamped dead-wax matrix

Drop all the photos in one folder and run the tool. It groups them into albums,
reads each with Claude vision, finds the release on Discogs, and — crucially —
uses the runout matrix to pin down the *exact pressing* rather than guessing.
It defaults to a dry run and will only add a record when it's confident.

---

## How it decides (and when it adds)

Per album, the pipeline:

1. **Preps images** — front/back downscaled to ~1568px (token savings); the
   runout is kept high-res and run through grayscale + autocontrast so etched
   characters read better.
2. **Extracts with Claude vision** (one tool-forced call): front artist/title;
   back label/catno/barcode/format/country/year/notes; and a literal,
   character-by-character transcription of the runout with a confidence flag.
   The same call independently classifies each image as front/back/runout.
3. **Searches Discogs**, starting tight and relaxing only if nothing exact is
   found: `barcode` → `catno + artist` → `artist + title + format=Vinyl` →
   (looser fallbacks) catno alone, title-only, broad full-text. Artist credits
   are reduced to the searchable primary artist first ("Norman Brooks with Al
   Goodman and His Orchestra" → "Norman Brooks").
4. **Disambiguates by runout** — fetches each candidate's release detail and
   fuzzy-matches your transcribed matrix against its `Matrix / Runout`
   identifiers. A runout match pins the *exact pressing* and overrides the rest.
5. **Confirms by cover art** — when the runout can't confirm the pressing, it
   pools candidates across *every* search angle (not just the one that hit),
   then asks Claude vision whether any candidate's Discogs cover is the same
   album as your front-cover photo (robust to angle, glare, stickers). It
   compares up to 16 covers across two batches and lets the vision be the
   filter — so messy classical/compound titles still get matched. A cover match
   confirms the *right album* (pressing may differ); it then picks the most-held
   pressing sharing that cover.
6. **Falls back** to the master's most-common version (highest "have", US-biased)
   as a text-only *guess* when neither runout nor cover resolves it.

**Tiers — tight first, relax only as needed:**

| Tier | Confidence | Trigger | Action |
|---|---|---|---|
| **exact** | **HIGH** ✓ | barcode exact, or runout matrix match | **add** (exact pressing) |
| **cover** | **COVER** ◉ | front-cover photo visually matches a candidate's art | **add** (right album, pressing may differ) |
| **general** | **MEDIUM** ✓ | single strong `catno + artist`, front/back agree | **add** |
| **liberal** | **GUESS** ≈ | best text-only candidate, nothing confirmed it | **flag** → `review.csv` (with candidate link) |
| — | **LOW** ⚑ | nothing plausible / not found | **flag** → `review.csv` |

The bar for adding is "it's the right album" — something it can pin exactly
*or* confirm by cover art. A text-only guess isn't good enough to add: it's
flagged for review instead (still shown as ≈ GUESS with its best-candidate link
so you can one-click confirm it). Disable the cover-vision step with
`--no-cover` (saves a vision call per unconfirmed album).

**Sequence integrity:** the order is not trusted blindly. Vision confirms every
group is one front, one back, one runout. If a group doesn't match (a missed or
extra shot drifted the sequence), the run **stops and reports the exact group**
so you can fix it — it never silently adds wrong records.

---

## Setup

Requires **Python 3.11+**.

```bash
git clone https://github.com/JDStraughan/discogser.git
cd discogser
python3.11 -m venv .venv
source .venv/bin/activate

pip install -e .            # installs the `discogser` command
# pip install -e ".[heic]"  # add this if you shoot iPhone HEIC photos

cp .env.example .env        # then edit .env (see below)
```

### `.env`

| Variable | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key. |
| `ANTHROPIC_MODEL` | Vision model. Defaults to `claude-sonnet-4-6` if unset. |
| `DISCOGS_TOKEN` | Personal access token — [Discogs developer settings](https://www.discogs.com/settings/developers). |
| `DISCOGS_USERNAME` | Your Discogs username. |
| `DISCOGS_FOLDER` | Collection folder to add to (default `Uncategorized`). |
| `USER_AGENT` | **Required.** A unique, descriptive UA with contact info, e.g. `discogser/1.0 +mailto:you@example.com`. Discogs throttles hard without one. |

---

## Verify the sequence check first

Before pointing it at real photos, confirm the grouping and role-validation
logic works (no API calls, no network):

```bash
python selftest.py
```

You should see all checks pass, including a clean group being accepted and
drifted groups (missed/extra shot) being rejected.

For the full offline test suite (matching, resolver tiers, grouping, the
per-album state machine — all with mocked Discogs/vision, no network):

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

---

## Run

```bash
# Dry run (default): process everything, write reports, add NOTHING.
discogser ./photos

# Actually add (exact + cover-confirmed albums) to your collection.
discogser ./photos --commit

# Add to a specific folder by name; skip the cover-vision step.
discogser ./photos --commit --folder "New Arrivals" --no-cover
```

> No install? `python -m discogser ./photos` works from a clone too.

### Watching it run

The output is built for scanning a long run without eye strain. A pinned bottom
bar shows position, %, elapsed, ETA, and a live tally; aligned, color-accented
rows scroll above it — one line per album, never wrapping:

```
╭─ discogser ──────────────────────────────────────────────────────────╮
│ mode    DRY-RUN                                                       │
│ albums  1000                                                          │
│ folder  New Arrivals (id 4321)                                        │
│ owned   137 already in your collection                               │
╰──────────────────────────────────────────────────────────────────────╯
  #     ·  conf    artist — title                 release    value     signal
[  1/1000] ✓ HIGH   Pink Floyd — The Dark Side of the Moon   r1873013    $42.99  barcode + runout match (100)
[  2/1000] ✓ HIGH   Miles Davis — Kind of Blue               r5288476    $1,250  barcode exact
[  3/1000] ✓ MEDIUM Neil Young — Harvest                     r2391445    $18.00  catno + artist (single, runo…
[  4/1000] ⚑ LOW    Fleetwood Mac — Rumours                  r999111          —  ambiguous (best guess) · guess
[  5/1000] ↻ DUP    Talking Heads — Remain in Light          r44444       $9.99  already in collection
[  6/1000] ✗ ERR    IMG_0042..IMG_0044                       —                —  vision failed: …
cataloguing ━━━━━━━━━━━━━━━━━━━━ 42% 423/1000 • 0:07:11 elapsed • 0:09:48 left  ✓310 ●44 ⚑31 ↻29 ✗9
```

Each row shows **artist — title**, the **release id**, the **value** (Discogs'
lowest current marketplace listing in USD — `—` when nothing's for sale), and
the match signal. Color is an accent (status glyph, confidence badge, release
id, money-green value), so the album text stays readable. The tally on the bar
is `✓added ●medium ⚑review ↻skipped ✗errors`. Anything you must not miss —
**sequence drift** and an **incomplete set** — is a full-width red panel that
halts the run, and the close-out is a color-coded summary panel.

### Outputs (written into the photos folder)

- **`results.csv`** — every album: chosen `release_id`, title, year, format,
  `value_usd` + `num_for_sale`, confidence, signal used, alternates, Discogs
  URL, source image filenames, runout text, and the `model` used.
- **`review.csv`** — the LOW-confidence stragglers with best-candidate links so
  you can one-click and finish them by hand.
- A summary at the end: added, flagged, skipped (dupes), errors, and total
  Claude token usage. Add `--verbose` (or `--log-file FILE`) for debug logging.

---

## Safety, idempotency, resumability

- **Dry-run by default.** Nothing is written without `--commit`.
- **Never processed twice.** A SQLite ledger (`ledger.sqlite3`) is keyed by the
  content hash of an album's 3 image files (order-independent), so reruns skip
  albums already added. Dry-run results are recorded but not marked committed,
  so a later `--commit` still adds them.
- **Dedupe against your collection.** Your existing collection is pulled once at
  startup; releases you already own are skipped.
- **Cached Discogs calls.** Release and master responses are cached on disk
  (`.cache/`) so reruns and multi-candidate checks don't re-query.
- **Rate-limit aware.** Requests are paced under the 60/min authenticated limit,
  read `X-Discogs-Ratelimit-Remaining`, and back off exponentially on 429/5xx.
  Vision calls run at `temperature=0` (deterministic) with SDK retries.
- **Hardened by default.** Cover-image downloads are SSRF-allowlisted to Discogs
  hosts and size/type-checked; image decoding is bomb-capped; secrets are
  redacted from errors. See [SECURITY.md](SECURITY.md).

---

## Module map

| Module | Responsibility |
|---|---|
| `discogser/cli.py` | CLI entry point / argument parsing (the `discogser` command). |
| `discogser/pipeline.py` | Orchestration, search/disambiguation ladder, confidence policy, reports. |
| `discogser/ui.py` | Console rendering: live progress bar + tally, aligned color-coded rows, panels. |
| `discogser/vision.py` | Image prep + Claude vision extraction, role classification, cover matching. |
| `discogser/discogs.py` | Discogs client: search, release/master fetch, collection writes, caching, throttling. |
| `discogser/matching.py` | Runout normalization + fuzzy matching; front/back agreement. |
| `discogser/ledger.py` | SQLite ledger keyed by image-content hash. |
| `discogser/config.py` | `.env` / environment loading. |
| `selftest.py` | Offline smoke check for grouping + sequence-integrity validation. |
| `tests/` | `pytest` suite: matching, resolver tiers, grouping, cataloguer (mocked). |

## License

Released into the public domain under [The Unlicense](LICENSE) — do whatever you
want with it, no attribution required.

> Note: `photos/` is git-ignored. Your album photos are personal data (phone
> photos carry EXIF GPS); keep them out of the repo.
