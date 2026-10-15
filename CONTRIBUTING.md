# Contributing to Quorum

Thanks for helping. This is the short path from clone to merged pull request.

## Set up

```bash
docker compose up -d falkordb          # the graph database
pip install -r requirements.txt ruff mypy hypothesis pytest-timeout
cd frontend && npm ci && cd ..
```

Full setup, including production: [docs/SETUP.md](docs/SETUP.md).

## Before you open a pull request

Run the same checks CI runs. A pull request merges only when both jobs are green.

| Area | Command |
|---|---|
| Backend lint | `ruff check .` |
| Backend types | `mypy` (uses `mypy.ini`, checks `backend/app`) |
| Backend tests | `pytest -q` |
| Frontend (types, lint, tests, contrast, contract, builds) | `cd frontend && npm run verify` |
| Accessibility (all screens, desktop and phone) | `cd frontend && npm run qa:a11y` |

## How we work

- Branch from `main`, keep a pull request to one idea, and describe what changed and why.
- Add or update a test with every behavior change. The execute guard, handoff claim and quarantine paths are safety-critical: they need a test that fails without the change.
- The console speaks plain English. New words go in [`frontend/src/copy/glossary.ts`](frontend/src/copy/glossary.ts) so they appear in the in-app Guide (press `G`).
- Respect `prefers-reduced-motion` and keep axe-core at zero serious violations.
- Do not commit secrets. Use `.env.example` as the template; real values stay in your environment.

## Reporting a problem

Bugs and ideas: open an issue. Security problems: follow [SECURITY.md](SECURITY.md) and do not file a public issue.
