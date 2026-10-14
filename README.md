# VPN Platform — Backend

FastAPI backend: source of truth for VPN business logic (keys, payments, 3x-UI, YooKassa).
Extracted from the `platform/` monorepo; see [CLAUDE.md](CLAUDE.md) for architecture,
the standalone-deployment note (networking/DB/`shared/`/secrets), and API details.

## Quick start (dev)

```bash
cp .env.example .env.dev   # fill in values
docker compose -f docker-compose.dev.yml up -d --build
```

## Quick start (prod)

```bash
cp .env.example .env       # fill in values; BOT_SECRET_KEY/ADMIN_API_KEY must match platform/.env
docker compose up -d --build
```

The stack is self-contained: `postgres` (its own volume `backend_postgres_data`)
and `backend` share the internal `backend_net` network. No external networks and
no dependency on `platform/`. A fresh volume is initialized from
`db/schema_fixed.sql`; migrating existing data from the old platform Postgres is
a separate step (see CLAUDE.md).

## Tests / Lint

```bash
make test
make lint
```
