# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue.

- Preferred: GitHub → this repo's **Security** tab → **Report a vulnerability**
  (private advisory).
- Or email **jdstraughan@gmail.com** with details and, ideally, a reproduction.

We aim to acknowledge within 72 hours and to ship a fix or mitigation promptly.

## Threat model & hardening

`discogser` runs locally, reads your photos, and talks to the Anthropic and
Discogs APIs using your personal tokens. The main untrusted inputs are the
images it decodes and the cover-image URLs returned by the Discogs API. Current
hardening:

- **SSRF defense** — cover-image downloads are restricted to `https` Discogs
  hosts via an allowlist, redirects are followed manually and re-validated each
  hop, and responses are content-type + magic-byte checked.
- **Decompression-bomb / DoS defense** — a hard `Image.MAX_IMAGE_PIXELS` cap and
  a per-download byte ceiling bound memory and disk use.
- **Secret hygiene** — tokens load from `.env` (git-ignored, real env wins) and
  are redacted from any error text; they are never written to the cache or
  ledger.
- **CSV injection** — report cells beginning with a spreadsheet formula trigger
  are neutralized.
- **No injection surface** — SQLite uses parameterized queries; output paths are
  fixed names under your photos folder (no model/API data reaches a filesystem
  path).

## Dependencies

Runtime dependencies are version-pinned and continuously scanned: `pip-audit`
runs in CI, CodeQL performs static analysis, and Dependabot opens PRs for
security updates.

## Your data

Never commit your `.env` or your `photos/` (phone photos carry EXIF GPS). Both
are git-ignored by default; keep them out of any manual archive too.
