# Releasing

`discogser` publishes to PyPI automatically when you push a version tag. It uses
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/), so there
are no API tokens to create, store, or rotate.

## One-time setup (per project)

1. Create the project on PyPI (or reserve the name) at <https://pypi.org>.
2. On PyPI, go to the project's **Settings → Publishing** and add a trusted
   publisher with:
   - Owner: `JDStraughan`
   - Repository: `discogser`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. In the GitHub repo, create an **Environment** named `pypi`
   (Settings → Environments). Optionally add a required reviewer so a human
   approves each publish.

## Cutting a release

1. Bump the version in `pyproject.toml` (and `discogser/__init__.py`).
2. Commit, then tag and push:

   ```bash
   git commit -am "Release vX.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   ```

3. The **Release** workflow builds, runs `twine check`, and publishes to PyPI.
   Within a minute, `pip install discogser` installs the new version.

## Local sanity build

```bash
python -m build
twine check dist/*
pip install dist/discogser-*.whl   # smoke-test the wheel
```
