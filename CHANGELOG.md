# Changelog

All notable changes to discogser are documented here. This project follows
[Semantic Versioning](https://semver.org).

## [1.0.0]

First public release.

### Catalog engine
- Reads each album from three phone photos (front, back, side-A runout) with
  Claude vision, in one tool-forced call per album.
- Tight-to-loose matching ladder: barcode and dead-wax runout matrix (exact
  pressing), then visual cover-art confirmation (right album), then a text-only
  guess that is flagged rather than added.
- Sequence-integrity gate: confirms each group is two covers plus a runout, and
  halts with the exact group if a missing or extra shot drifted the sequence.
- Dry-run by default; `--commit` to add. Idempotent SQLite ledger keyed by
  image-content hash; dedupes against your existing collection; on-disk caching
  and a rate-limit-aware Discogs client.

### Interfaces
- `discogser` CLI with a live, color-coded progress UI and `results.csv` /
  `review.csv` reports.
- `discogser --doctor` preflight: verifies config, that both API keys work, and
  that a photo folder groups cleanly, before a real run spends anything.
- `discogser-web` (the `[web]` extra): drag-and-drop photos in the browser,
  watch results stream live, download reports, and **resolve flagged albums by
  clicking the matching cover** (adds the chosen pressing on the spot).
- Friendly, loud warning when iPhone HEIC photos are present without the
  `[heic]` extra installed.

### Hardening
- SSRF-allowlisted, size- and type-checked cover downloads; decompression-bomb
  caps; secret redaction; CSV formula-injection neutralized.
- Deterministic vision calls (`temperature=0`) with SDK retries and timeouts.
- CI on Python 3.11-3.13 with ruff, mypy, pytest, `pip-audit`, and CodeQL;
  Dependabot for updates.
