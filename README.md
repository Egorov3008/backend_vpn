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

Requires the platform's external network to already exist (`platform_default`,
created by `platform/docker-compose.yml`) and, on first deploy of an existing
production stack, the `platform_postgres_data` volume to already hold the data
(see the Postgres migration note in CLAUDE.md).

## Tests / Lint

```bash
make test
make lint
```
