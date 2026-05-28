# Contributing to FirstCall

Thanks for your interest. FirstCall is a voice AI for medical emergencies — every improvement matters.

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — used for dependency management
- Redis — `brew install redis` (macOS) or `apt install redis-server` (Linux)
- Git

---

## Setup

**1. Fork the repo**

Click **Fork** at the top of [github.com/Harish1120/FirstCall](https://github.com/Harish1120/FirstCall), then clone your fork:

```bash
git clone https://github.com/<your-username>/FirstCall.git
cd FirstCall
```

**2. Install dependencies**

```bash
uv sync
```

**3. Set up environment variables**

```bash
cp .env.example .env
```

For most `good first issue` tasks (triage tests, keyword cleanup, lifespan migration, health check), you don't need real API keys — the unit tests run without any external services.

**4. Start Redis**

```bash
# macOS
brew services start redis

# Linux
sudo systemctl start redis-server
```

**5. Run the tests**

```bash
uv run pytest
```

All 25 tests should pass. If they do, you're ready to contribute.

---

## Making a change

**1. Create a branch**

```bash
git checkout -b fix/your-change-name
```

**2. Make your changes**

See the issue for the exact file(s) to touch and what to write. Each `good first issue` lists the file, the code, and the command to verify it works.

**3. Check your code**

```bash
uv run ruff check .       # lint
uv run ruff format .      # format
uv run mypy .             # type check
uv run pytest             # tests
```

All four must pass before submitting. The CI will run the same checks on your PR.

**4. Commit**

```bash
git add <files>
git commit -m "fix: short description of what you changed"
```

Use a prefix: `fix:` for bug fixes, `feat:` for new functionality, `test:` for test additions, `chore:` for cleanup.

**5. Push and open a PR**

```bash
git push origin fix/your-change-name
```

Then open a pull request against `main` on [github.com/Harish1120/FirstCall](https://github.com/Harish1120/FirstCall). Reference the issue number in the PR description (e.g. `Closes #5`).

---

## Good first issues

| Issue | File to touch | Effort |
|-------|--------------|--------|
| [#5 — Expand triage test coverage](https://github.com/Harish1120/FirstCall/issues/5) | `tests/test_triage.py` | ~1h |
| [#6 — Migrate to lifespan handler](https://github.com/Harish1120/FirstCall/issues/6) | `app/main.py` | ~20 min |
| [#7 — Health check: verify Redis](https://github.com/Harish1120/FirstCall/issues/7) | `app/main.py` | ~30 min |
| [#8 — Deduplicate keyword lists](https://github.com/Harish1120/FirstCall/issues/8) | `app/triage.py` | ~5 min |

Start with the issue that matches your comfort level. Leave a comment on the issue so others know you're working on it.

---

## Questions

Open a [GitHub Discussion](https://github.com/Harish1120/FirstCall/discussions) or leave a comment on the relevant issue.
