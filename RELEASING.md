# Releasing

Pushing a version tag builds the package, creates a GitHub Release with the
wheel/sdist attached, and (once enabled) publishes to PyPI via
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) - no API tokens
to store. Tagging is always safe: PyPI publishing is gated behind a repo
variable, so a release before PyPI is set up just produces the GitHub Release.

## Cut a release (works today)

1. Bump the version in `pyproject.toml` and `discogser/__init__.py`.
2. Tag and push:

   ```bash
   git commit -am "Release vX.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   ```

The **Release** workflow builds, `twine check`s, and publishes a GitHub Release
with downloadable artifacts. Users can install straight from it:
`pip install https://github.com/JDStraughan/discogser/releases/download/vX.Y.Z/discogser-X.Y.Z-py3-none-any.whl`.

## Enable `pip install discogser` (one-time PyPI setup)

Requires your pypi.org account:

1. Reserve the project on [pypi.org](https://pypi.org).
2. Project -> **Settings -> Publishing** -> add a Trusted Publisher:
   owner `JDStraughan`, repo `discogser`, workflow `release.yml`, environment `pypi`.
3. In the GitHub repo: create an **Environment** named `pypi`
   (Settings -> Environments), and set a repo **Variable** `PUBLISH_TO_PYPI` =
   `true` (Settings -> Secrets and variables -> Actions -> Variables).

The next tag then also publishes to PyPI automatically.

## Local sanity build

```bash
python -m build && twine check dist/*
pip install dist/discogser-*.whl   # smoke-test the wheel
```
