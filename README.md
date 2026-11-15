# Safety Portal (independent rebuild)

A new implementation of the Paneltec Safety Portal, built from the written reconstruction pack only. It is separate from the existing portal: it has its own database, its own secrets and synthetic demo data. Nothing here connects to the live system.

**Current state:** see `docs/PROGRESS.md` for exactly what is built and tested so far, `docs/STAGED_PLAN.md` for the full plan and `docs/ledger/` for status per requirement and per API operation.

## What is in the repository

| Path | Contents |
|---|---|
| `backend/` | FastAPI service, PostgreSQL models, Alembic migrations, tests |
| `frontend/` | React + TypeScript office web app |
| `docs/ADR-001-architecture.md` | Stack choice and every recorded decision |
| `docs/STAGED_PLAN.md` | The nine build stages |
| `docs/DESIGN_SYSTEM.md` | Colours, type and interface rules |
| `docs/DATA_MODEL.md` | Tables and relationships |
| `docs/openapi.json` | OpenAPI generated from the running handlers |
| `docs/ledger/` | Feature and endpoint coverage ledgers |
| `docker-compose.yml`, `.env.example` | Deployment |

## Run with Docker (NAS, Portainer or Umbrel)

```bash
cp .env.example .env          # then set POSTGRES_PASSWORD and SETUP_KEY (two long random values)
docker compose build
docker compose run --rm api python -m app.seed generate-key   # paste the result into .env as ENCRYPTION_KEY
docker compose up -d
```

`PUBLIC_BASE_URL` is the address people use to reach the portal (for example `https://portal.example.com.au`). Set it before printing site QR signs or sending invitation links: both embed that address.

`COMMS_SAFE_MODE` is `on` unless you change it, which means the portal never sends an email or text: it prepares them and keeps them in the Outbox. Leave it on until the Microsoft 365 and TextMagic connections are set up and tested and you want real messages to go out.

`ENCRYPTION_KEY` protects sensitive details in the database (dates of birth, tax file numbers, and the passwords and keys for connections). Keep a copy somewhere safe and separate from the database backups: if it is lost or changed, the protected details already saved cannot be read again. Everything else keeps working without it, but protected details cannot be saved or shown.

Open `http://<server>:3960`. An empty install shows the **first-time setup** screen: enter the `SETUP_KEY`, the organisation name, and your own name, email and password. That person becomes the **account holder**: the protected top-level admin and the payroll owner. Setup works once.

If the account holder is ever locked out, an operator can create another admin from the server:

```bash
docker compose exec api python -m app.seed create-admin --org "<organisation name>" --name "Name" --email person@example.com
```

Optional synthetic demo data (two organisations, nine invented people each):

```bash
docker compose exec -e DEMO_PASSWORD='choose-a-demo-password' api python -m app.seed demo
```

Database migrations run automatically each time the `api` container starts. Business data lives in two volumes, `db-data` (database) and `files-data` (uploaded files); rebuilding or replacing the containers does not touch them. Back up both together.

> The Docker files were written but could not be run in the build environment (no Docker daemon there). The same code was run directly, as described below.

## Run for development

Needs Python 3.13, Node 22 and PostgreSQL 16.

```bash
# database (once)
createuser portal --pwprompt && createdb portal -O portal && createdb portal_test -O portal

# API
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+psycopg://portal:<password>@localhost:5432/portal
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --port 8000

# web app (second terminal)
cd frontend
npm ci
npm run dev        # http://localhost:5173, proxies /api to port 8000
```

API documentation is served at `/api/docs`.

## Tests

```bash
cd backend
TEST_DATABASE_URL=postgresql+psycopg://portal:<password>@localhost:5432/portal_test .venv/bin/python -m pytest
cd ../frontend && npm run build     # type-check and production build
cd .. && ./e2e/run.sh               # browser walkthrough of every built screen on a fresh database
```

The backend suite runs against a real PostgreSQL database that it migrates with Alembic and empties before each test. It covers allowed and denied paths, two organisations with identically named people, single-use links, session revocation and that the migrations match the models.

## Safety boundaries

- No email, SMS, payment or lodgement is sent by this build. Invitation and reset links are shown once to the admin and logged as "held".
- Payroll access is denied to everyone except the payroll owner (the account holder by default); an ordinary admin has none.
- Secrets come from `.env`, which is never committed. There are no default passwords.
