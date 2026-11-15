# Contributing to Brain Autopilot

Thank you for your interest in contributing to **Brain Autopilot**! Whether you are fixing a bug, adding a new detector, improving documentation, or proposing a feature, your contributions are welcome.

---

## Ways to Contribute

- **Report Bugs:** Open an issue using the Bug Report template with reproduction steps and environment details.
- **Suggest Features:** Open a Feature Request issue or start a discussion in GitHub Discussions.
- **Improve Detectors & Engines:** Add or refine bug detectors in `brain_autopilot/detectors.py` or probe capabilities in `brain_autopilot/engines/`.
- **Add Benchmarks & Tests:** Expand test coverage in `tests/` or benchmark scenarios in `bench/`.
- **Improve Documentation:** Clarify CLI usage, workflows, or examples in [README.md](README.md).

---

## Development Setup

1. **Fork & Clone** the repository:

   ```bash
   git clone https://github.com/<your-username>/brain-autopilot.git
   cd brain-autopilot
   ```

2. **Create a Virtual Environment** (Python 3.10+ required):

   ```bash
   python -m venv .venv
   # Linux / macOS:
   source .venv/bin/activate
   # Windows PowerShell:
   .venv\Scripts\Activate.ps1
   ```

3. **Install in Editable Mode with Dev Dependencies:**

   ```bash
   python -m pip install --upgrade pip
   python -m pip install -e ".[dev]"
   ```

---

## Running Tests & Validation

Before opening a pull request, make sure all checks pass locally:

```bash
# Unit and integration tests
python tests/run_tests.py
pytest -q

# Repository consistency validator (versions, relative links, syntax, install.ps1 ASCII)
python tests/validate_repo.py

# Built-in benchmark suite
python bench/run_bench.py

# CLI smoke check
brain-autopilot --version
brain-autopilot codex . --no-update
```

---

## Project Conventions

- **Zero Runtime Dependencies:** Core `brain_autopilot` modules must use only the Python standard library (`dependencies = []` in [pyproject.toml](pyproject.toml)).
- **Python Compatibility:** Target Python 3.10+ with `from __future__ import annotations` and clear type hints.
- **Windows Compatibility:** Keep `install.ps1` strictly ASCII-only (enforced by [validate_repo.py](tests/validate_repo.py)) so it works on Windows PowerShell 5.1.
- **Version Sync:** If you bump the package version, keep `__init__.py`, [pyproject.toml](pyproject.toml), [plugin.json](plugin.json), [SKILL.md](skills/brain-autopilot/SKILL.md), and [CHANGELOG.md](CHANGELOG.md) in sync.
- **Commit Messages:** Use clear, descriptive commit messages (e.g., `fix: ...`, `feat: ...`, `docs: ...`, `test: ...`).

---

## Pull Request Process

1. Create a new branch from `main` (e.g., `git checkout -b fix/my-fix` or `feat/my-feature`).
2. Make focused, well-tested changes.
3. Run the full test and validation suite (`pytest -q`, `python tests/run_tests.py`, `python tests/validate_repo.py`).
4. Open a Pull Request against `main` and fill out the PR template.

---

## Community Standards

Please note that this project is released with a [Code of Conduct](CODE_OF_CONDUCT.md). By participating in this project, you agree to abide by its terms.
