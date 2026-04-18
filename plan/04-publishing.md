# Publishing to PyPI — step-by-step

This is an actionable checklist for cutting a public release of
`androidperf`. Read in order; each step links to the prior one.

---

## 0. One-time setup (first release only)

### 0.1 Claim the name
1. Go to <https://pypi.org/account/register/> and register a PyPI account.
2. Enable 2FA (PyPI now requires it for uploads). Use TOTP or a hardware key.
3. Register at <https://test.pypi.org/account/register/> too — TestPyPI is a
   separate service with a separate account. You'll use it as a dress
   rehearsal.
4. Check that `androidperf` is actually available:
   `https://pypi.org/project/androidperf/` should 404. If taken, rename the
   project (update `pyproject.toml` `name`, the entry point, the `src/`
   directory, and every import — a bigger change than it looks).

### 0.2 API tokens (don't use your password)
1. <https://pypi.org/manage/account/token/> → create a token scoped to "Entire
   account" for the first upload (after that, narrow to the project).
2. Do the same on TestPyPI.
3. Store both in `~/.pypirc`:

   ```ini
   [distutils]
   index-servers =
       pypi
       testpypi

   [pypi]
   username = __token__
   password = pypi-AgEIcHlwaS5vcmcCJGU...

   [testpypi]
   repository = https://test.pypi.org/legacy/
   username = __token__
   password = pypi-AgENdGVzdC5weXBpLm9yZwIkZTU...
   ```

   `chmod 600 ~/.pypirc`. Do not commit this file.

4. For CI (GitHub Actions etc.), use **trusted publishing** instead of storing
   tokens as secrets — PyPI verifies the workflow's OIDC token and issues a
   short-lived upload token. Configure it at
   <https://pypi.org/manage/project/androidperf/settings/publishing/> once the
   project exists.

---

## 1. Prepare the project metadata

### 1.1 Fill in `pyproject.toml`
The file today has the bones. Before first upload, add:

```toml
[project]
name = "androidperf"
version = "0.1.0"
description = "ADB-based Android performance metric recorder with live TUI and HTML report"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "Your Name", email = "you@example.com" }]
keywords = ["android", "adb", "performance", "profiling", "fps", "cli"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Software Development :: Debuggers",
    "Topic :: Software Development :: Testing",
    "Topic :: System :: Monitoring",
]

[project.urls]
Homepage     = "https://github.com/<you>/android-performance"
Repository   = "https://github.com/<you>/android-performance"
Issues       = "https://github.com/<you>/android-performance/issues"
Changelog    = "https://github.com/<you>/android-performance/blob/main/CHANGELOG.md"
```

`license = { text = "MIT" }` is the short form; if you want PyPI to show the
full text, add a `LICENSE` file at the repo root and use
`license = { file = "LICENSE" }`.

### 1.2 Add a LICENSE file
`LICENSE` at the root. MIT template:
<https://opensource.org/licenses/MIT>. Year + your name.

### 1.3 Lock the Python floor
`requires-python = ">=3.11"` is what you built against. Don't lower it without
testing — some f-string, `UTC`, and typing features we use need 3.11+.

### 1.4 Decide version scheme
Use semver. `0.x` versions signal "API may break". Bump rules:
- **Patch** (0.1.0 → 0.1.1): bug fixes, new fixtures, doc tweaks.
- **Minor** (0.1.0 → 0.2.0): new metric, new CLI flag, JSON field additions.
- **Major** (0.1.0 → 1.0.0): removing a CLI command, changing sample-key names,
  breaking `samples.json` shape. Avoid until you're happy with the surface.

### 1.5 Changelog
Create `CHANGELOG.md` at root. "Keep a Changelog" format:

```markdown
# Changelog

## [0.1.0] - 2026-04-18
### Added
- Initial release: CPU / RAM / network / FPS / battery / thermal / screen
  transitions, live Rich dashboard, self-contained HTML report.
```

---

## 2. Pre-flight checks (every release)

```bash
# 1. All tests pass
pytest -q

# 2. No lint errors
ruff check src tests

# 3. README renders on PyPI — it uses a restricted Markdown subset
pip install readme-renderer[md]
python -m readme_renderer README.md -o /tmp/readme.html
open /tmp/readme.html

# 4. Version bump
#    Edit pyproject.toml `version`, edit src/androidperf/__init__.py if it
#    mirrors that, update CHANGELOG.md.

# 5. Clean working tree
git status        # should be clean before tagging
```

---

## 3. Build the distribution

```bash
pip install --upgrade build twine
rm -rf dist build *.egg-info      # clear any stale artifacts
python -m build                    # produces dist/androidperf-0.1.0.tar.gz
                                   # and   dist/androidperf-0.1.0-py3-none-any.whl
twine check dist/*                 # validates metadata, README rendering
```

Expected output:
```
Checking dist/androidperf-0.1.0-py3-none-any.whl: PASSED
Checking dist/androidperf-0.1.0.tar.gz: PASSED
```

If `twine check` flags a README problem, fix it in `README.md` (most common:
relative links that PyPI can't resolve, unsupported HTML). Rebuild.

---

## 4. Upload to TestPyPI first

```bash
twine upload --repository testpypi dist/*
```

Verify end-to-end from a clean environment:

```bash
python3.11 -m venv /tmp/verify && source /tmp/verify/bin/activate
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            androidperf
androidperf version       # prints 0.1.0
androidperf devices       # sanity check the entry point actually runs
deactivate
```

The `--extra-index-url https://pypi.org/simple/` is important — TestPyPI
doesn't have our runtime dependencies (pandas, plotly, etc.), so without it
install fails.

If anything's off (missing file, broken import, wrong metadata), you cannot
overwrite an existing version on either index — bump to `0.1.0.post1` or
`0.1.1` and re-upload.

---

## 5. Upload to real PyPI

```bash
twine upload dist/*
```

Then tag and push:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Verify:

```bash
pipx install androidperf
androidperf version
```

Project page: `https://pypi.org/project/androidperf/`.

---

## 6. (Optional, recommended) Automate via GitHub Actions

Once the manual flow works, automate it. Rough shape of
`.github/workflows/publish.yml`:

```yaml
name: publish

on:
  push:
    tags: ["v*"]

jobs:
  build-and-publish:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # for PyPI trusted publishing
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: python -m pip install --upgrade build
      - run: python -m build
      - uses: pypa/gh-action-pypi-publish@release/v1
        # no `password:` needed — trusted publishing uses OIDC
```

Configure PyPI trusted publishing
(<https://docs.pypi.org/trusted-publishers/>) once, and from then on every
`git tag vX.Y.Z && git push --tags` publishes.

---

## 7. After publishing

- **Watch for issues.** The first week of a release is when surface problems
  show up (weird device outputs, wheels on unusual platforms).
- **Announce** on wherever your audience is — Hacker News / Reddit r/androiddev
  / your blog. Keep the pitch honest: it's a local dev tool, not a CI gate.
- **Update the README badge row** with PyPI version + download count if
  desired (`https://img.shields.io/pypi/v/androidperf`).
- **Yank bad releases** with `pip install twine`:
  `twine upload --skip-existing` for corrections, or
  <https://pypi.org/manage/project/androidperf/releases/> to yank.

---

## Gotchas / things easy to miss

- **The project name on PyPI is case- and separator-normalized.** `androidperf`,
  `android-perf`, `Android_Perf` all resolve to the same slug. Decide once.
- **Readme images won't render on PyPI if they're relative paths.** Use
  absolute GitHub raw URLs, or omit them.
- **`[dev]` extras aren't published.** They're fine for repo dev. For users,
  only the `dependencies` list matters.
- **Don't pin dependencies tightly in `dependencies=[...]`.** `adbutils>=2.8.0`
  is fine; `adbutils==2.8.3` will start causing resolver pain.
- **`adb` is NOT a Python dep.** It's a system binary — the README has to
  tell users to install it.
- **Shipped wheels are `py3-none-any` (universal).** No native code → no per-OS
  wheels needed. Keep it that way; it's a big reason installs are fast.
- **Never upload secrets.** `twine` reads `~/.pypirc` and env vars; make sure
  `.env` / personal token files are in `.gitignore` (they are, but double-check
  before `python -m build` sweeps everything into the sdist).
