# Contributing to discogser

Thanks for helping catalog the world's vinyl. This is a small, focused tool —
contributions that keep it simple and trustworthy are very welcome.

## Dev setup

```bash
git clone git@github.com:JDStraughan/discogser.git
cd discogser
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,heic]"
```

## Before you open a PR

```bash
ruff check .     # lint (and `ruff check --fix .` to auto-fix)
pytest           # full offline test suite — no network, no API keys needed
```

CI runs exactly these on Python 3.11–3.13. Both must pass.

The test suite mocks Discogs and the vision model entirely (`tests/helpers.py`),
so you can develop the whole matching ladder without an API key or a single
network call. New behavior should come with a test.

## Code style

- Match the surrounding code. Type hints on public functions; relative imports
  inside the `discogser/` package.
- Keep the matching ladder honest: a record is only **added** when it's pinned
  exactly (barcode/runout) or visually confirmed by cover art. Anything weaker
  is **flagged for review**, never silently added. Don't loosen that without a
  very good reason — trust is the whole point.
- Be kind to the Discogs API: respect the rate limiter and the on-disk cache.

## Reporting a sequence-drift or mismatch

If the tool halts on "sequence drift" or picks the wrong release, please open an
issue with the per-album console line and (if you can) the relevant rows from
`results.csv` / `review.csv`. **Do not attach your photos** — they carry EXIF
GPS and other personal data.

## License

By contributing you agree your work is released into the public domain under
[The Unlicense](LICENSE), the same terms as the rest of the project.
