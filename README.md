# MediMind

MediMind is a prototype platform for evidence-linked personal medical records. It takes scattered medical documents (lab reports, prescriptions, discharge summaries) and turns them into health records that are structured, searchable and in date order. Every fact in a record links back to the source document it came from.

**Intended users:** patients and caregivers.

## What MediMind is not

MediMind manages health information. It is not a clinical tool. It does **not**:

- diagnose conditions,
- prescribe medicines or recommend medication changes,
- make clinical decisions on its own,
- fill in missing medical information, or settle conflicting records without telling you.

All demo data is **fictional and synthetic**.

## Status

**Phase 1: Development environment and project foundation.** The project foundation is in place: configuration, logging, error handling, a health check and tests, plus a local PostgreSQL + pgvector database and an authenticated, read-only database connection. There are no AI features yet. See [docs/PROGRESS.md](docs/PROGRESS.md).

### Roadmap
| Phase | Focus |
|---|---|
| 0 | Product planning and system design |
| 1 | Development environment and project foundation |
| 2 | Medical document intelligence |
| 3 | Structured clinical extraction |
| 4 | Database and longitudinal intelligence |
| 5 | Advanced hybrid RAG |
| 6 | Healthcare intelligence features |
| 7 | LangGraph workflows |
| 8 | Evaluation, security and reliability |
| 9 | Frontend integration, deployment and portfolio documentation |

For details, see [docs/ROADMAP.md](docs/ROADMAP.md).

## Getting started

Requirements: Windows, macOS or Linux, with [uv](https://docs.astral.sh/uv/) installed. uv provides Python 3.12.

```bash
uv sync                          # create .venv and install the locked dependencies
uv run python -m app health      # check the environment: exit code 0 = healthy
uv run pytest                    # run the tests
uv run ruff check .              # lint
```

Create your local settings file (git-ignored) from the template:

| Shell | Command |
|---|---|
| Windows CMD | `copy .env.example .env` |
| PowerShell | `Copy-Item .env.example .env` |
| macOS / Linux / Git Bash | `cp .env.example .env` |

- **Settings** use the `MEDIMIND_` prefix. A real environment variable overrides `.env`, and `.env` overrides the defaults in code.
- **VS Code (optional, local only):** `.vscode/` is git-ignored, so it isn't included when you clone the repo. To have VS Code load `.env` into new terminals, add `"python.terminal.useEnvFile": true` to your own `.vscode/settings.json`. If you change `.env`, open a new terminal so the change is picked up.
- **Health-check exit codes:** `0` healthy, `1` a check failed, `2` invalid configuration.

## Database (Docker)

MediMind runs PostgreSQL 18 with the pgvector extension in Docker. Only the database runs in Docker; the app runs natively (ADR-009). It needs [Docker Desktop](https://docs.docker.com/desktop/) with the WSL2 backend.

**Image:** `pgvector/pgvector:0.8.7-pg18-trixie`, pinned by its multi-platform digest `sha256:ab49524d18df7200b74ac9a475e1db6297fc1eb57da4c214c63d10ecc4b754fa`. To update it, look up the new tag's digest (`docker buildx imagetools inspect <tag>`) and change the tag and digest together in `docker-compose.yml`.

### 1. Configure `.env`
Copy the `MEDIMIND_DB_*` lines from `.env.example` into your `.env`, and **set your own strong password** in `MEDIMIND_DB_PASSWORD`.
- The value in `.env.example` is deliberately empty. Docker Compose refuses to start the database until a password is set.
- `.env` is git-ignored. Never commit a real password.
- Keep `MEDIMIND_DB_HOST=127.0.0.1`. The port is bound to IPv4 loopback only, and on Windows `localhost` can resolve to IPv6 first.

### 2. Everyday commands
Run these from the project root:

| Task | Command |
|---|---|
| Start (in the background) | `docker compose up -d` |
| Status (look for `healthy`) | `docker compose ps` |
| Stop (keeps all data) | `docker compose stop` |
| Start again after `stop` | `docker compose start` |
| Logs | `docker compose logs db` (add `-f` to follow) |
| SQL shell | `docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'` |
| Check the app can log in | `uv run python -m app health` (the `database_tcp` and `database_connection` lines) |

The `psql` command connects through the container's local socket, so you don't need to type the password.

**What the database health lines mean:**

| Line | Checks | Status |
|---|---|---|
| `database_tcp` | The port accepts TCP connections (no login) | ok, or **warn** if stopped |
| `database_password` | `MEDIMIND_DB_PASSWORD` is set (the value is never shown) | ok, or **warn** |
| `database_connection` | The app logs in with your settings and runs one read-only query | ok; **warn** if unreachable or no password is set (just start the database); **fail** if the login is rejected |

A rejected login means a wrong user, wrong password or missing database. The driver doesn't say which, so check `MEDIMIND_DB_USER`, `MEDIMIND_DB_PASSWORD` and `MEDIMIND_DB_NAME`. Error messages and logs never include passwords or the driver's raw error text.

### Integration tests (optional, read-only)
`uv run pytest` never needs Docker. To test against the real database, start it first, then run the opt-in integration tests:
```bash
docker compose start
uv run pytest -m integration
docker compose stop
```
- They read your `.env` in memory and never print it.
- Every connection is read-only, and they run only `SELECT`/`SHOW`. Each test checks that no tables, extensions, roles or schemas changed.
- The wrong-password check uses a fake password, not yours.
- They skip themselves if the password isn't set or the database isn't running.

### 3. Verify pgvector is available (without enabling it)
```bash
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = '"'"'vector'"'"';"'
```
- You should see `vector | 0.8.7 |`. The empty `installed_version` means the extension is available but not enabled.
- Enabling it is done by a migration in Task 1.7.

### 4. Confirm the port is reachable from this machine only
```bash
docker compose port db 5432      # expected: 127.0.0.1:5432
```

### 5. Preserve your data
- Data lives in the Docker volume `medimind_pgdata`, which Docker stores outside this repo.
- `docker compose stop` / `start` and `docker compose down` / `up -d` keep the data.
- ⚠️ **`docker compose down -v` deletes the `medimind_pgdata` volume and ALL database data, permanently.** Don't use `-v` unless you really mean to wipe the database.

## Migrations and backups

The schema is created and changed only by the Alembic migrations in `migrations/versions/`. `alembic.ini` holds no database URL: every command connects with the settings in your `.env`.

Run the commands below from the project root, in **PowerShell or Git Bash** (not cmd: the `sh -c '…'` quoting needs one of those two). The database must be running (`docker compose start`), except for the SQL preview.

### 1. Check the current revision
```bash
uv run alembic current        # e.g. "0003 (head)"; empty output = no migrations applied yet
uv run alembic history        # every revision, newest first
uv run python -m app health   # the database_schema line: ok, warn (behind / not migrated) or fail
```

### 2. Preview the SQL (offline, no database needed)
```bash
uv run alembic upgrade head --sql
```
Prints the exact SQL an upgrade would run, without connecting anywhere. Read it before migrating.

### 3. Apply migrations
```bash
uv run python -m app migrate
```
- It only upgrades, to the latest revision. Running it again at the latest revision changes nothing.
- The whole upgrade runs in **one transaction**: if any step fails, nothing is applied.
- **Take a backup first** (section 5) once the database holds data you care about.

### 4. Downgrades need an exact confirmation
A downgrade **destroys data**: it drops the tables of every revision it undoes. Alembic refuses any downgrade unless you confirm the name of the database you are connected to:
```bash
uv run alembic -x confirm_downgrade=medimind downgrade -1
```
- `-x confirm_downgrade=…` goes **before** `downgrade`, and must match the connected database's name exactly (case-sensitive, given once). Otherwise the command stops with `FAILED: Refusing to downgrade database …` and changes nothing.
- This applies to every target, including `base`, `-1` and a specific revision. Moving the recorded revision backwards with `alembic stamp`, or `alembic stamp --purge`, needs the same confirmation. Forward stamps don't.
- `--sql` previews (`uv run alembic downgrade 0003:0002 --sql`) only print SQL and need no confirmation.
- **Back up before any downgrade** (section 5).

### 5. Create a backup
Backups use PostgreSQL's **custom format** (`-Fc`, compressed, restorable with `pg_restore`). Keep them **outside this repository**, in `%USERPROFILE%\MediMind-backups`. (`backups/` and `*.dump` are git-ignored as a safety net, because dumps can contain patient data.)

> ⚠️ **Windows PowerShell 5.1 corrupts binary output redirected with `>`** (it re-encodes it as text). Never write a dump with `docker compose exec … pg_dump … > file.dump`. Instead, write the dump **inside the container**, then copy it out with `docker compose cp`, as below.

PowerShell:
```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\MediMind-backups" | Out-Null
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/medimind_backup.dump'
docker compose cp db:/tmp/medimind_backup.dump "$env:USERPROFILE\MediMind-backups\medimind_2026-11-14_before_migrate.dump"
docker compose exec -T db rm /tmp/medimind_backup.dump
```
Git Bash: the same, but use `mkdir -p "$USERPROFILE/MediMind-backups"` and the destination `"$USERPROFILE/MediMind-backups/medimind_2026-11-14_before_migrate.dump"`.

Name each file with the date and the reason (for example `before_migrate`, `before_downgrade`).

### 6. Verify a backup by restoring it into a scratch database
A backup you have never restored is not proven. Restore it into a **separate** database, `medimind_restore_check`, never into `medimind`:
```powershell
docker compose cp "$env:USERPROFILE\MediMind-backups\medimind_2026-11-14_before_migrate.dump" db:/tmp/restore.dump
docker compose exec -T db sh -c 'pg_restore --list /tmp/restore.dump > /dev/null && echo "archive readable"'
docker compose exec -T db sh -c 'createdb -U "$POSTGRES_USER" medimind_restore_check'
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d medimind_restore_check --exit-on-error /tmp/restore.dump'
```
Compare the restored copy with the original. The revision, table count and key row counts must match:
```powershell
docker compose exec -T db sh -c 'for d in medimind medimind_restore_check; do psql -U "$POSTGRES_USER" -d "$d" -At -c "SELECT current_database(), (SELECT version_num FROM alembic_version), (SELECT count(*) FROM information_schema.tables WHERE table_schema = '"'"'public'"'"'), (SELECT count(*) FROM patients), (SELECT count(*) FROM documents)"; done'
```
Then remove the scratch copy. **Type the name exactly: never `dropdb … medimind`.**
```powershell
docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" medimind_restore_check && rm /tmp/restore.dump'
```

### 7. After a migration: verify
1. `uv run python -m app health`: `database_schema` is **ok** ("at revision … (latest)").
2. `uv run alembic current` shows the latest revision with `(head)`.
3. `uv run alembic check` reports no differences between the models and the database.
4. `docker compose restart db`, then `uv run alembic current` again: the revision persists.

### 8. Rollback and recovery
- **A migration fails:** it rolls back by itself (one transaction). Fix the cause and run `migrate` again.
- **A migration applied but is wrong, and the database holds no data you need:** take a backup anyway, then run a confirmed downgrade to the previous revision (section 4).
- **A migration applied and data must be recovered:** stop anything using the database, take a fresh backup of the current state, verify the earlier backup (section 6), then restore it, for example with `pg_restore -U "$POSTGRES_USER" -d medimind --clean --if-exists --exit-on-error /tmp/restore.dump` (run inside the container after copying the dump in). Then repeat the checks in section 7.
- **Never use `docker compose down -v` as a recovery step.** It deletes the whole volume, including every database in it, permanently. Recovery always goes through migrations or a verified backup.

## Repository layout

| Path | Purpose |
|---|---|
| `docs/` | Project brief, roadmap, decisions and progress log |
| `app/` | Application code. `app/main.py` is the entry point, and `app/core/` holds configuration, logging, errors and health checks |
| `app/records/` | Database access (SQLAlchemy + psycopg). The **only** code allowed to talk to the database (ADR-016) |
| `tests/` | Automated tests (pytest). `tests/integration/` needs the running database and is opt-in |
| `data/synthetic/` | Fictional demo records only. Never put real patient data here. |

## Documentation

- [Project brief](docs/PROJECT_BRIEF.md): vision, scope, safety boundaries
- [Roadmap](docs/ROADMAP.md): phases and milestones
- [Architecture](docs/ARCHITECTURE.md): layers, data flows, RAG design, security, evaluation
- [User flows](docs/USER_FLOWS.md)
- [Database design](docs/DATABASE_DESIGN.md)
- [Decisions](docs/DECISIONS.md): architecture decision records
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md)
- [Progress](docs/PROGRESS.md)
- [Daily summary](SUMMARY.md): day-by-day log of work and decisions

