# DataRescue AI: project handoff

Snapshot taken 2026-10-11. This file summarizes where the project stands and how to pick it
up in a new session. The README is the main project documentation; this document links to
it rather than repeating it.

## Current state

| Item | Value |
|---|---|
| Repository | https://github.com/saxipatel/datarescue-ai (public) |
| Branch | `main`; local and `origin/main` are identical (0 ahead, 0 behind) |
| Latest commit | [`2767d1b`](https://github.com/saxipatel/datarescue-ai/commit/2767d1ba10b4d325a8b638f8644730c00231298b) "Update screenshot spec to capture the README images" |
| Latest CI | [run 38166572856](https://github.com/saxipatel/datarescue-ai/actions/runs/38166572856): all 4 jobs succeeded (details below) |
| Uncommitted changes | None in tracked files. This handoff file is new and not committed. |
| Local stack | Docker Compose project `datarescue`, running (postgres, redis, api, worker, scheduler, frontend) |
| Optional LLM diagnosis | Disabled (`LLM_ENABLED=false`); keep it off unless deliberately enabled |

Commit history:

| Commit | Change |
|---|---|
| `4a1de7c` | Initial commit |
| `57e1738` | CI fix: Alembic importable without `PYTHONPATH`; actions v7 |
| `b1e5d66` | CI badge and docs |
| `6beb3ce` | Safe dependency updates, test and tooling warning fixes |
| `03fc34a` | README rewrite with 11 real screenshots from the running app |
| `2767d1b` | `screenshots.spec.ts` writes the README filenames |

CI results for `2767d1b`:

| Job | Result |
|---|---|
| backend | 172 passed, 89% coverage |
| dbt | 23 of 23 pass |
| frontend | lint, typecheck and build pass |
| e2e | chaos demo 1,431 predicted = 1,431 written; Playwright 3 passed, 1 skipped (the opt-in screenshot spec) |

## What is persistent and what is temporary

### Persistent on this Mac

| What | Where | Notes |
|---|---|---|
| Project source (git working copy) | `~/Documents/datarescue-ai` | 205 tracked files, all pushed to GitHub |
| `.env` | `~/Documents/datarescue-ai/.env` | gitignored; currently identical to `.env.example` (development placeholders only) |
| Generated, gitignored files | `data/fixtures/` (4.8 MB), `data/benchmarks/`, `dbt/target/`, `dbt/logs/`, `frontend/dist/`, `frontend/node_modules/` (148 MB), tool caches | All can be regenerated |
| PostgreSQL data | Docker named volume `datarescue_pgdata` (about 270 MB) | All batches, incidents, quarantine, recovery jobs and audit history |
| Uploaded and generated datasets | Docker named volume `datarescue_appdata` (about 7 MB) | Mounted at `/data` in api, worker and scheduler |
| Redis data | Docker named volume `datarescue_redisdata` | Queue state only; nothing permanent lives here |
| Docker volumes on disk | Inside Docker Desktop's disk image (`~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw`) | Kept across restarts. They are lost only with `make reset`, `docker compose down -v`, `docker volume rm`, or a Docker Desktop "Clean / Purge data" / uninstall. |
| Export archives | `~/Documents/datarescue-ai-export.zip`, `~/Desktop/datarescue-ai.zip` | Snapshots from the earlier export step; older than the current commit |
| Original spec | `~/Desktop/DataRescue_AI_Autonomous_Build_Specification.docx` | |
| Remote copy | GitHub `saxipatel/datarescue-ai` | Code, docs and screenshots only. It never contains the database, `.env` or generated data. |

Local database contents at handoff:

| Record | Count |
|---|---|
| Batches | 17 |
| Incidents | 5 |
| Recovery jobs | 4 |
| Simulations | 5 |
| Quarantine rows | 2,000 |
| Audit rows | 89 |

The duplicate-test incident from 16:21 UTC on 2026-10-11 (`e505b075…`) is intentionally
left in `ACTION_REQUIRED` with no repair proposals. It is the expected state for an incident
waiting for a human to propose and approve a repair. Do not resolve it without the owner's
approval.

### Temporary (not needed to resume)

- **Claude session scratch folders** (under `/private/tmp`): screenshot capture scripts, rendered
  previews, test logs and the test-output screenshots. macOS clears these. Everything that
  matters was copied into the repository.
- **The scratch workspace** where the project was first built. The desktop app may delete it
  after a week without changes. The repository in `~/Documents` is the complete copy.
- **Isolated test stacks** (Compose projects such as `drshot`) used for verification. They were
  removed together with their volumes and never touched the `datarescue` volumes.
- **One detail:** the running `datarescue-redis-1` container was created while the project still
  lived in the scratch workspace. It only uses the named volume `datarescue_redisdata`, so it
  doesn't depend on that folder. The next `make up` from `~/Documents/datarescue-ai` may
  recreate it, and the volume is kept.

## Architecture in brief

| Component | Stack and role |
|---|---|
| API | FastAPI (Python 3.12, Pydantic v2, SQLAlchemy 2, Alembic) on :8000; stores state and enqueues work |
| Worker | RQ worker on Redis 7; runs `ingest_batch_task`, `diagnose_task` and `recovery_task` (`backend/app/workers/tasks.py`) |
| Scheduler | Separate process; freshness checks and the stale recovery-job reaper |
| Database | PostgreSQL 16, the system of record. It enforces `event_id` uniqueness, CHECK constraints and an append-only audit trigger. |
| Frontend | React 18 + TypeScript + Vite + Tailwind; served by nginx on :5173, which proxies `/api` |
| Analytics | dbt-postgres 1.8: staging view, revenue and settlement marts |
| Optional | Kafka/Redpanda consumer and Airflow DAG (Compose profiles), plus LLM enrichment (off) |

How a batch moves through the system:

1. Ingestion streams 1,000-row chunks with a checkpoint.
2. Each row lands in raw evidence before anything else happens.
3. Rows are validated against the contract and an event-time watermark.
4. The database enforces dedup: identical repeats are skipped, conflicting ones quarantined.
5. Rows that fail validation are quarantined with reason codes.
6. Batch quality rules run; a breach opens an incident.
7. A deterministic diagnosis explains the incident and lineage shows its impact.

Repairs go through proposal, dry run and steward approval, then execute under an idempotency
key with an advisory lock. Verification checks must pass before the incident resolves.

More detail:

- [README.md](README.md): diagrams and screenshots;
- [docs/architecture.md](docs/architecture.md);
- [docs/decisions/](docs/decisions/): ADRs 0001 to 0005;
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md): the acceptance matrix.

## Setup and everyday commands

Requirements: Docker Desktop (Compose v2.24+) with at least 4 GB of memory, GNU make and git.
No other tools are needed on the host.

```bash
cd ~/Documents/datarescue-ai
make up           # start the stack with existing data (volumes are kept)
make ps           # status
make logs         # follow api, worker, scheduler
make down         # stop, keep data
```

- Console: http://localhost:5173
- API docs: http://localhost:8000/docs
- Readiness: http://localhost:8000/api/v1/ready

The demo accounts and password are listed in the README's Quick start. They are for local
use only.

**Commands that destroy data.** Run these only with the owner's explicit approval:

- `make reset`, which asks you to type `reset`;
- `docker compose down -v`;
- `docker volume rm datarescue_*`.

For a fresh clone on another machine, run `make setup` and then `make up`. It seeds a new,
separate database.

## Test commands

| Command | What it runs | Writes to the live database? |
|---|---|---|
| `make test` | backend pytest (unit + integration) and frontend lint, typecheck and build | No. Integration tests use a separate test database. |
| `make lint` | ruff, format check, mypy, eslint, tsc | No |
| `make dbt` | `dbt build` (3 models, 20 tests) | It rebuilds the analytics marts from accepted data |
| `make e2e` | Playwright against the running stack | **Yes.** The walkthrough injects a fault and runs a recovery. |
| `make demo` / `make demo-chaos` | scripted fault → diagnosis → approval → recovery | **Yes** |
| `make benchmark` | 1k / 10k / 100k ingestion benchmark | No. It uses a separate benchmark database. |

**To verify without changing the local data,** run e2e, demo or screenshot runs on an isolated
copy:

1. Export the code into a scratch folder with `git archive HEAD | tar -x -C <dir>`.
2. Start that copy with `docker compose -p <other-name>` and an override file that changes the
   host ports (`ports: !override [...]`).
3. Run the tests there.
4. Remove it with `docker compose -p <other-name> down -v`.

**Screenshots.** `frontend/e2e/screenshots.spec.ts` regenerates the 11 README images. It is
opt-in (`SCREENSHOTS=1`), and `SCREENSHOT_DIR` changes where it writes. It signs in through
the UI, which adds one login audit event. To avoid that, run it against an isolated stack.
The current README images were captured with read-only requests and a session token minted
inside the api container, so no login was recorded.

## Known issues and open items

- **React Router 6:** 2 moderate advisories. The fix needs React Router 7, a major upgrade
  that the owner deferred.
- **Dev and build tooling:** 9 advisories in the Vite and Tailwind 3 dependency chains. The fixes need
  Vite or Tailwind major versions, also deferred. They affect dev only, not the built bundle.
- **Network exposure (issue 6, not fixed):**
  - Compose publishes Postgres 5432, Redis 6379, API 8000 and frontend 5173 on all
    interfaces.
  - Postgres uses a fixed development password, and Redis has no authentication.
  - Binding to `127.0.0.1` and adding a frontend healthcheck were proposed but not approved.
- **GitHub Actions:** `ubuntu-latest` moves to Ubuntu 26 on 2026-10-19. Watch the first CI
  run after that date.
- **Local `frontend/node_modules`:** contains stray duplicate files (`index 2.js` and similar)
  from a Finder or iCloud copy. They aren't tracked and don't affect CI. `npm ci` in
  `frontend/` would clean them up.
- **`.env`** still has the development placeholder `JWT_SECRET`. That's fine locally, but it
  must be replaced before any shared deployment (see [docs/security.md](docs/security.md)).
- **Feature limits** (by design, documented in the README):
  - batch-level detection;
  - dataset-level lineage;
  - heuristic diagnosis;
  - LLM path not tested with a live key;
  - `RETRY_MISSING_BATCH` only works for the synthetic source;
  - the audit trigger can be bypassed by a superuser.

## Resuming in a new Claude Code session

1. Open `~/Documents/datarescue-ai` as the working folder.
2. Check the state:
   ```bash
   git status -sb && git log --oneline -3
   docker compose ps
   ```
   If the stack is stopped, run `make up`.
3. Give the new session context, for example:

   > Read PROJECT_HANDOFF.md, README.md and IMPLEMENTATION_STATUS.md. Then [task].

4. Restate the working rules the owner has used so far:
   - do not delete, reset or overwrite the local database or existing records;
   - leave the 16:21 incident unresolved;
   - keep LLM diagnosis disabled;
   - do not expose credentials;
   - run tests that write data on an isolated copy;
   - no commits or pushes without approval;
   - no Co-Authored-By lines in commits;
   - no major-version dependency upgrades without approval.
