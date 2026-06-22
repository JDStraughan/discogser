# discogser

[![CI](https://github.com/JDStraughan/discogser/actions/workflows/ci.yml/badge.svg)](https://github.com/JDStraughan/discogser/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Unlicense](https://img.shields.io/badge/license-Unlicense-blue.svg)](LICENSE)

Catalog your vinyl into [Discogs](https://www.discogs.com) from phone photos.

Snap three photos of each record, point the tool at the folder, and it does the
rest: it reads every sleeve with Claude vision, finds the release on Discogs,
and uses the dead-wax runout to pin the **exact pressing** instead of guessing.
It runs as a dry run by default and only adds a record when it is sure.

## How you shoot

Photograph each album as **exactly three shots, in this order**, then repeat for
the next record:

1. **Front cover**
2. **Back cover**
3. **Side A runout**, a macro close-up of the etched or stamped dead-wax matrix

Put every photo in one folder. The tool sorts them, groups them into albums, and
confirms each group really is one front, one back, and one runout before it
trusts the sequence.

## How it decides

For each album the pipeline runs five steps, tightest signal first:

1. **Read the images.** One vision call extracts the front (artist, title), the
   back (label, catalog number, barcode, format, country, year, notes), and a
   literal, character-by-character transcription of the runout with a confidence
   flag. The same call classifies each photo as front, back, or runout.
2. **Search Discogs**, starting precise and relaxing only if nothing exact
   turns up: barcode, then catalog number plus artist, then artist plus title,
   then broader fallbacks. Printed credits are reduced to a searchable primary
   artist first (for example, "Norman Brooks with Al Goodman and His Orchestra"
   becomes "Norman Brooks").
3. **Match the runout.** Each candidate's catalogued `Matrix / Runout`
   identifiers are fuzzy-matched against your transcription. A runout match pins
   the exact pressing and beats every weaker signal.
4. **Confirm by cover art.** When the runout cannot settle the pressing, the
   tool pools candidates from every search angle and asks Claude vision whether
   any candidate's Discogs cover is the same album as your photo. It compares up
   to 16 covers and lets the vision be the filter, so messy classical and
   compound titles still resolve. A cover match confirms the right album, then
   picks the most widely held pressing that shares that cover.
5. **Guess, and flag it.** If nothing confirms the album, the most common
   version of the master (US-biased) is recorded as a guess for your review,
   never added automatically.

### When it adds versus flags

| Tier | Badge | Trigger | Result |
|---|---|---|---|
| Exact | `HIGH` | Barcode match, or runout matrix match | **Added** (exact pressing) |
| Cover | `COVER` | Your cover photo matches a candidate's art | **Added** (right album, pressing may differ) |
| General | `MEDIUM` | A single strong catalog-number plus artist hit | **Added** |
| Liberal | `GUESS` | Best text-only candidate, nothing confirmed it | **Flagged** to `review.csv` |
| Miss | `LOW` | Nothing plausible, or not found | **Flagged** to `review.csv` |

The bar for adding is simple: it has to be the right album, proven by an exact
identifier or by the cover art. A text-only guess is never good enough to add on
its own. It is flagged for review with a one-click candidate link instead. Pass
`--no-cover` to skip the cover step and save a vision call per unconfirmed album.

**Sequence integrity.** The order is never trusted blindly. If a group is not
one front, one back, and one runout (a missed or extra shot that shifted the
sequence), the run stops and reports the exact group so you can fix it. It will
not silently add the wrong records.

## Setup

Requires **Python 3.11+**. Install with pipx (recommended) or pip:

```bash
pipx install "discogser[heic]"      # or: pip install "discogser[heic]"
```

> **📱 Shooting on an iPhone? Keep the `[heic]` extra.** iPhones save HEIC by
> default, and without this support every photo fails to decode. Plain
> `discogser` (no `[heic]`) is only for JPEG/PNG shooters. (The tool warns you
> loudly if it sees HEIC photos it can't read.)

To run from a clone instead (for development):

```bash
git clone https://github.com/JDStraughan/discogser.git
cd discogser
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[heic,dev]"
```

### Configuration

Create a `.env` file **in the directory you'll run `discogser` from** (it's
searched for upward from there) with these variables. From a clone you can
`cp .env.example .env` to start:

| Variable | What it is |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key. |
| `ANTHROPIC_MODEL` | Vision model. Defaults to `claude-sonnet-4-6` if unset. |
| `DISCOGS_TOKEN` | Personal access token from [Discogs developer settings](https://www.discogs.com/settings/developers). |
| `DISCOGS_USERNAME` | Your Discogs username. |
| `DISCOGS_FOLDER` | Collection folder to add to. Defaults to `Uncategorized`. |
| `USER_AGENT` | **Required.** A unique, descriptive value with contact info, such as `discogser/1.0 +mailto:you@example.com`. Discogs throttles hard without one. |

### Check it before you run it

The preflight confirms your config is present, that both API keys actually work,
and that a photo folder groups cleanly, so a real run never starts misconfigured:

```bash
discogser --doctor ./photos
```

```
Config
  ✓ ANTHROPIC_API_KEY    set
  ✓ DISCOGS_USERNAME     yourname
Connectivity
  ✓ Anthropic            key valid, claude-sonnet-4-6 responded
  ✓ Discogs              authenticated as yourname
Photos (./photos)
  ✓ 5 albums, no leftovers

All good. You're ready to run.
```

## Run

```bash
# Dry run (default): process everything, write reports, add nothing.
discogser ./photos

# Add the exact and cover-confirmed albums to your collection.
discogser ./photos --commit

# Add to a named folder, and skip the cover-vision step.
discogser ./photos --commit --folder "New Arrivals" --no-cover
```

No install? `python -m discogser ./photos` works straight from a clone.

### Browser UI

Prefer not to live in a terminal? Install the web extra and run a small local
app:

```bash
pip install -e ".[web]"   # or: pip install "discogser[web]"
discogser-web             # opens on http://127.0.0.1:8765
```

Drag your photos onto the page (or paste a folder path), choose dry run or
commit, and watch the same matching engine stream results into a live,
color-coded table with clickable Discogs links and downloadable reports.

For the flagged stragglers, you do not have to juggle Discogs tabs: each one
shows a **pick** link that opens its candidate covers right in the table. Click
the pressing that matches your record and it is added to your collection on the
spot (and recorded so a later run will not re-flag it).

The server binds to localhost only, since it uses your tokens; do not expose it
to a network.

### Watching it run

The output is built for scanning a long run without eye strain. A pinned bottom
bar shows position, percent, elapsed time, ETA, and a live tally. Aligned,
color-accented rows scroll above it, one line per album, and never wrap:

```
╭─ discogser ──────────────────────────────────────────────────────────────────────────╮
│ mode     DRY-RUN                                                                       │
│ albums  1000                                                                           │
│ folder  New Arrivals (id 4321)                                                         │
│ owned   137 already in your collection                                                 │
╰────────────────────────────────────────────────────────────────────────────────────────╯
  #     ·  conf    artist - title   release    value     signal
[   1/1000] ✓ HIGH   Pink Floyd - The Dark Side of the M…  r1873013     $42.99  barcode + runout match
[   2/1000] ✓ HIGH   Miles Davis - Kind of Blue            r5288476     $1,250  barcode exact
[   3/1000] ◉ COVER  The Swingle Singers - Going Baroque   r555123      $12.00  cover match
[   4/1000] ✓ MEDIUM Neil Young - Harvest                  r2391445     $18.00  catno + artist (single)
[   5/1000] ≈ GUESS  Norman Brooks - Sings                 r888          $3.00  best guess
[   6/1000] ⚑ LOW    Rimsky-Korsakov - Scheherazade        -                 -  not found
[   7/1000] ↻ DUP    Talking Heads - Remain in Light       r44444        $9.99  already in collection
[   8/1000] ✗ ERR    IMG_0042..IMG_0044                    -                 -  vision failed
cataloguing  42%  423/1000 . 0:07:11 elapsed . 0:09:48 left   ✓310 ◉44 ≈31 ⚑20 ↻29 ✗9
```

Every row shows the artist and title, the release id, the value (Discogs' lowest
current marketplace listing in USD), and the match signal. Color is used as an
accent on the glyph, badge, release id, and price, so the album text stays easy
to read. Anything you must not miss, such as a sequence drift or an incomplete
set, appears as a full-width panel that halts the run, and the close-out is a
color-coded summary.

### Outputs (written into the photos folder)

- **`results.csv`**: every album, with its chosen `release_id`, title, year,
  format, `value_usd`, `num_for_sale`, confidence, signal, alternates, Discogs
  URL, source filenames, runout text, and the model used.
- **`review.csv`**: the low-confidence stragglers, each with a best-candidate
  link so you can finish them by hand.
- A summary at the end covering added, flagged, skipped, errors, and total
  Claude token usage. Add `--verbose` (or `--log-file FILE`) for debug logging.

## Safety and reliability

- **Dry run by default.** Nothing is written without `--commit`.
- **Never processed twice.** A SQLite ledger (`ledger.sqlite3`) is keyed by the
  content hash of an album's three photos, independent of file order, so reruns
  skip records already added. Dry-run results are recorded but not committed, so
  a later `--commit` still adds them.
- **Dedupes against your collection.** Your collection is pulled once at startup,
  and releases you already own are skipped.
- **Caches Discogs calls.** Release and master responses are cached on disk so
  reruns and multi-candidate checks do not re-query.
- **Respects the rate limit.** Requests are paced under the 60-per-minute
  authenticated limit, read `X-Discogs-Ratelimit-Remaining`, and back off
  exponentially on errors. Vision calls run at `temperature=0` with SDK retries.
- **Hardened by default.** Cover-image downloads are restricted to Discogs hosts
  and checked for size and type, image decoding is capped against decompression
  bombs, and secrets are redacted from error output. See [SECURITY.md](SECURITY.md).

## Development

The full test suite is offline. It mocks Discogs and the vision model, so no API
keys or network access are needed.

```bash
pip install -e ".[dev]"
ruff check .                 # lint
mypy                         # type check
pytest                       # tests
pip-audit --skip-editable    # dependency vulnerability scan
```

`python selftest.py` is a quick no-network check of the grouping and
sequence-integrity logic. Run it once before pointing the tool at real photos.

## Project layout

| Module | Responsibility |
|---|---|
| `discogser/cli.py` | Command-line entry point, argument parsing, and `--doctor`. |
| `discogser/doctor.py` | Preflight checks: config, API connectivity, photo grouping. |
| `discogser/web.py` | Optional local browser UI (`discogser-web`), streaming over SSE. |
| `discogser/pipeline.py` | Orchestration, the search and matching ladder, confidence policy, reports. |
| `discogser/ui.py` | Console rendering and the `Reporter` protocol both front ends share. |
| `discogser/vision.py` | Image prep, Claude vision extraction, role classification, cover matching. |
| `discogser/discogs.py` | Discogs client: search, fetch, collection writes, caching, throttling. |
| `discogser/matching.py` | Runout normalization, fuzzy matching, front/back agreement. |
| `discogser/ledger.py` | SQLite ledger keyed by image-content hash. |
| `discogser/config.py` | Environment and `.env` loading. |
| `tests/` | Pytest suite, fully mocked. |

## License

Released into the public domain under [The Unlicense](LICENSE). Do whatever you
like with it, no attribution required.

> Your `photos/` folder is git-ignored on purpose. Phone photos carry EXIF GPS
> and other personal data, so keep them out of the repository.
