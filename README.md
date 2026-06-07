# discogser

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
   downloads each candidate's Discogs cover and asks Claude vision whether it's
   the same album as your front-cover photo (robust to angle, glare, stickers).
   A cover match confirms the *right album* (pressing may differ).
6. **Falls back** to the master's most-common version (highest "have", US-biased)
   as a text-only *guess* when neither runout nor cover resolves it.

**Tiers — tight first, relax only as needed:**

| Tier | Confidence | Trigger | Action |
|---|---|---|---|
| **exact** | **HIGH** ✓ | barcode exact, or runout matrix match | auto-add (exact pressing) |
| **cover** | **COVER** ◉ | front-cover photo visually matches a candidate's art | auto-add (right album, pressing may differ) |
| **general** | **MEDIUM** ✓ | single strong `catno + artist`, front/back agree | auto-add |
| **liberal** | **GUESS** ≈ | best text-only candidate, nothing confirmed it | add **only with `--guess`** |
| — | **LOW** ⚑ | nothing plausible / not found | **not added** → `review.csv` |

By default it adds everything it can pin exactly *or* confirm by cover — that's
the bar for "it's the right album." Pure text guesses are held back unless you
pass `--guess`. Disable the cover-vision step with `--no-cover` (saves a vision
call per unconfirmed album).

**Sequence integrity:** the order is not trusted blindly. Vision confirms every
group is one front, one back, one runout. If a group doesn't match (a missed or
extra shot drifted the sequence), the run **stops and reports the exact group**
so you can fix it — it never silently adds wrong records.

---

## Setup

Requires **Python 3.11+**.

```bash
cd discogser
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env (see below)
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

---

## Run

```bash
# Dry run (default): process everything, write reports, add NOTHING.
python catalog.py ./photos

# Actually add (exact + cover-confirmed albums) to your collection.
python catalog.py ./photos --commit

# Also commit text-only best-guesses (right album, maybe wrong pressing).
python catalog.py ./photos --commit --guess

# Add to a specific folder by name; skip the cover-vision step.
python catalog.py ./photos --commit --folder "New Arrivals" --no-cover
```

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
  URL, source image filenames, runout text.
- **`review.csv`** — the LOW-confidence stragglers with best-candidate links so
  you can one-click and finish them by hand.
- A summary at the end: added, flagged, skipped (dupes), errors.

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

---

## Module map

| File | Responsibility |
|---|---|
| `catalog.py` | CLI entry point / argument parsing. |
| `main.py` | Pipeline orchestration, search/disambiguation ladder, confidence policy, reports. |
| `ui.py` | Console rendering: live progress bar + tally, aligned color-coded rows, panels. |
| `vision.py` | Image prep + Claude vision extraction and role classification. |
| `discogs.py` | Discogs client: search, release/master fetch, collection writes, caching, throttling. |
| `matching.py` | Runout normalization + fuzzy matching; front/back agreement. |
| `ledger.py` | SQLite ledger keyed by image-content hash. |
| `config.py` | `.env` / environment loading. |
| `selftest.py` | Offline checks for grouping + sequence-integrity validation. |
