# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Backend API

FastAPI backend serving as the source of truth for VPN service business logic.

## Standalone deployment (extracted from the platform monorepo)

This repository was extracted from the `platform/` monorepo (which still hosts `bot/`
and `web/`) via `git filter-repo`, keeping full commit history. It has its own
Dockerfile, `docker-compose.yml`/`docker-compose.dev.yml`, and CI
(`.github/workflows/ci.yml`) — but for **production it is not network-independent**:
it depends on `platform/`'s Postgres and docker network being reachable, and only
`docker-compose.dev.yml` (its own throwaway `postgres_dev`) is truly standalone.

- **Networking:** `docker-compose.yml` attaches `backend` to a single network — the
  platform's existing external network `platform_net` (real name: `platform_default`,
  created by `platform/docker-compose.yml`). This is what lets `bot`/`web`/`nginx` in
  `platform/` keep resolving `backend:8000` by service name, and what lets `backend`
  resolve `postgres:5432` (below) — both only work because `backend` and `platform/`
  run on the **same host**, attached to the same local docker network. There is no
  overlay/cross-host networking set up; moving `platform/` to a different server while
  leaving `backend` on this one would break both directions of that name resolution.
- **Database:** `backend` has **no Postgres of its own** in production — `DATABASE_URL`
  points at `postgres:5432`, the platform monorepo's own Postgres container, resolved
  by service name over `platform_net`. (An earlier revision gave `backend` its own
  `postgres` service on a zero-copy-reused `platform_postgres_data` volume; that ran
  *two* Postgres processes against the same data directory concurrently and corrupted
  the WAL — reverted in `f1ec094`, do not reintroduce a local `postgres` service in
  `docker-compose.yml`.) `DB_USER`/`DB_PASSWORD`/`DB_NAME` in `.env` must match the
  platform monorepo's Postgres credentials. Schema DDL (`db/schema_fixed.sql`) and the
  `tools/db/` backup/restore/migration tooling are still relevant for the dev compose's
  isolated `postgres_dev`, not for prod.
- **`shared/`:** this repo has its own copy of `shared/` (was a bind-mount in the
  monorepo). `platform/bot/` keeps its own separate copy. **There is no automated sync**
  — when `shared/config/core.py` changes in one place, mirror it manually:
  `rsync -a --delete platform/shared/ platform-backend/shared/`, review the diff, run
  tests before committing on both sides.
- **Secrets:** `BOT_SECRET_KEY`, `ADMIN_API_KEY`, and `DB_NAME`/`DB_USER`/`DB_PASSWORD`/
  `DATABASE_URL` must match the values in `platform/.env` (read by `bot`/`web`/its
  `postgres`) — no automated sync, rotate by hand in both places.
- **Public HTTPS (test/dev box only):** `docker-compose.yml` also runs a `caddy`
  service that publishes `backend` to the internet directly — automatic Let's Encrypt
  TLS for `BACKEND_DOMAIN` (`Caddyfile`, reverse-proxying to `backend:8000`), host
  ports via `CADDY_HTTP_PORT`/`CADDY_HTTPS_PORT` (default 80/443 — required for the
  ACME HTTP-01 challenge unless you switch to DNS-01). All three vars live in `.env`,
  read by `docker-compose.yml` for variable substitution (not by the `backend` app).
  This is deliberately **not** the `platform/`-nginx-as-single-edge pattern used
  elsewhere in this doc (see External API clients below) — it's the first piece of a
  target architecture where `backend` + the 3x-ui panel are a self-contained unit on
  one server, and bot/web/other API clients live on different servers entirely, so
  `backend` needs its own public ingress rather than depending on `platform/` being
  co-located.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run server (port 8000)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run all tests
pytest

# Run a single test file
pytest tests/api/test_keys.py

# Run a single test
pytest tests/api/test_keys.py::test_list_keys

# Fail-fast / coverage / one module (Makefile wrappers around pytest)
make test-fast
make test-cov            # HTML report at htmlcov/index.html
make test-module MODULE=api

# Lint (ruff check) / auto-fix + format
make lint
make formatting
```

## Architecture Overview

**Request Flow:**
```
Bot/Web API Client
    ↓
FastAPI Router (/api/v1/*)
    ├─ verify_bot_secret (X-Bot-Secret header check)
    ├─ Extract parameters (tg_id, email, etc.)
    └─ Call service/factory functions
    ↓
Service Classes (KeyCreation, PaymentProcessor, KeyRenewal, etc.)
    ├─ 3x-UI integration (native standalone API via client.py)
    ├─ YooKassa payment processing
    ├─ Cache invalidation
    └─ Database updates
    ↓
PostgreSQL + 3x-UI Panel
```

### Key Identifiers (Critical)

- **User** → `tg_id` (Telegram ID)
- **Key** → `email` (not id!) — unique identifier in 3x-UI
- **Payment** → `payment_id` (not id!) — YooKassa transaction ID

### Core Services

**`KeyCreation` (`services/core/payment/creation_service.py`):**
- Called when payment succeeds (webhook)
- Creates VPN key in 3x-UI, saves to DB, updates cache
- Only for paid tariffs (free keys created directly via `/keys/create` endpoint)

**`PaymentProcessor` (`services/core/payment/processor.py`):**
- Validates YooKassa webhooks
- Updates payment status in database
- Calls KeyCreation on success

**`KeyRenewal` (`services/core/keys/utils/renewal.py`):**
- Extends key expiry in 3x-UI and database
- Resets notification flags and traffic counters
- Callable from `/keys/{email}/renew` endpoint or payment flow

**`CacheService` (`services/cache/service.py`):**
- In-memory cache with TTL (loaded at startup)
- Holds users, tariffs, keys, servers, stocks
- Updated on every mutation (create/delete/renew key, update payment status)

### API Endpoints

#### Keys (`/api/v1/keys`)

- **GET `/?tg_id=...`** — List user's keys (paginated)
- **GET `/{email}`** — Get key details (includes client_id, key config, expiry, trial status)
- **POST `/create`** — Create new key (free tariffs only)
  - Requires: `tg_id`, `tariff_id`
  - Fails: 402 if tariff is paid (use payments flow instead)
- **POST `/trial`** — Create a free trial key (sets `user.trial = 1`)
  - Requires: `tg_id` (query param)
  - Optional: `gift_token` (query param) — applies gift if provided
  - Fails: 403 if trial already used
- **POST `/{email}/renew`** — Renew key expiry
  - Requires: `tg_id`, `tariff_id`, `number_of_months`
  - Fails: 402 if tariff is paid
- **DELETE `/{email}`** — Delete key (from 3x-UI and DB)
  - Requires: `tg_id` (ownership check)

#### Payments (`/api/v1/payments`)

- **GET `/?tg_id=...`** — Payment history
- **GET `/{payment_id}/status`** — Check payment status
  - Requires: `tg_id` (ownership check)
- **POST `/create`** — Initiate payment
  - Creates YooKassa invoice
  - Sets payment_type to encode operation (create_key|renew_key)
- **POST `/webhook`** — YooKassa webhook
  - Verifies IP whitelist
  - Updates payment status, calls KeyCreation on success

#### Tariffs (`/api/v1/tariffs`)

- **GET `/`** — List all tariffs
- **GET `/{id}`** — Get tariff details

#### Users (`/api/v1/users`)

- **GET `/{tg_id}`** — Get user info (server_id, ref_count, etc.)
- **POST `/`** — Register new user (auto-called by bot or web on first key creation)

#### Auth (`/api/v1/auth`)

- **POST `/register-from-invite`** — one-time invite-token registration for the web form (`INVITE_TOKEN` env var).
- **POST `/telegram-login`** — verifies a Telegram Login Widget payload (`app/core/telegram.py::verify_telegram_hash`) and issues/looks up the corresponding user.

#### Landing (`/api/v1/landing`)

Anonymous, cookie-based flow for the marketing landing page (separate from the bot/web `tg_id`-trusting flow above): `POST /set-ref-cookie`, `POST /quick-key` (mints a short-lived key on `XUI_INBOUND_ID_LANDING`), `GET /state`, `POST /mark-converted/{landing_uid}`, `POST /claim/{landing_uid}`. Signed cookies use `LANDING_COOKIE_SECRET` (falls back to `BOT_SECRET_KEY`).

#### Mobile MVP (`/api/v1/mobile`)

- **GET `/shared-config`** — One shared VPN config for the MVP Android app; no per-user auth, no accounts, no per-user state.
  - Auth: `X-App-Secret: <MVP_APP_SECRET>` header (checked by `verify_app_secret()`, **not** `verify_bot_secret()` — see Authentication exception below).
  - Resolves the single key at `MVP_SHARED_KEY_EMAIL`, downloads/parses its subscription URL (with an in-memory 5-minute TTL cache, since every caller gets the identical response), and returns `{vless_uri, expiry_time}`.
  - Fails: 500 if the shared key isn't configured/found (deploy/provisioning error); 502 if the upstream subscription download/parse fails.
  - Provisioning (one-time, manual): create a free tariff with `amount=0` and `limit_ip=0` (ideally a long `period`), call `POST /keys/create` once against it to mint the shared key, then paste that key's `email` into `MVP_SHARED_KEY_EMAIL`.

#### Admin (`/api/v1/admin`, `api/v1/admin.py`)

Split into two routers with different auth (see Authentication below):

- **`router`** (read-only + `verify_admin_or_bot`, i.e. bot secret also accepted): stats, scheduler status, maintenance-mode status, user/key/payment listings, gift/tariff/referral lookups.
- **`destructive_router`** (`verify_admin_actor` only — `X-API-Key` + `X-Admin-Tg-Id`): mutating ops — set maintenance mode, delete user/key, generate key, mass-renew, change key date/tariff, delete inactive users, start/poll a panel `sync` job, and `api-clients` create/list/revoke/rotate.

Grep `api/v1/admin.py` for the current full route list rather than trusting a manually maintained enumeration here — it grows frequently (referrals, gift codes, promotions, and sync jobs were all added after this section was first written).

### Authentication

**Service-to-Service:** `X-Bot-Secret: <BOT_SECRET_KEY>` header required on all endpoints (checked by `verify_bot_secret()` dependency), **except** `/api/v1/mobile/shared-config`, which uses its own static secret (`X-App-Secret: <MVP_APP_SECRET>`, checked by `verify_app_secret()`) — see Mobile MVP above.

**No user authentication** — backend trusts the `tg_id` parameter from the calling service (bot or web). The calling service is responsible for JWT validation.

**External API clients (public-api tag):** Unlike bot/web/mobile-mvp above, these don't use a static env-secret — `Authorization: Bearer <key>` against per-client keys stored (hashed) in the `api_clients` table, checked by `verify_api_client(required_scopes=[...])` (`app/auth.py`, `services/api_clients/service.py`). Keys are issued/listed/revoked/rotated via admin-only `POST/GET /admin/api-clients*` (`X-API-Key` + `X-Admin-Tg-Id`, same as other destructive admin ops). The raw key is only ever shown once, in the create/rotate response. Pilot endpoint: `GET /api/v1/public/tariffs` (scope `tariffs:read`). The platform monorepo's `nginx/default.conf.template` proxies `/api/v1/public/` straight to `backend` (not through `web`), rate-limited both at nginx (`limit_req zone=public_api`) and in-app (`app/rate_limit.py`).

### Database

**Ownership:** in production, `backend` connects to the platform monorepo's Postgres container (not one of its own) — see the Database bullet under Standalone deployment above for why, and for the host/network coupling that implies.

**Connection Pool:** asyncpg, created once at startup by `create_db_pool()` (`database/base.py`) and stored on `app.state.pool`. Injected per-endpoint via `Depends(get_pool)` (`app/dependencies.py`; also `get_cache` and `get_service_data` for the other two `app.state` singletons set up in `app/main.py`'s lifespan).

**`DataService`** (`database/service.py`) is the raw asyncpg query layer per entity; `LoadingService` (`services/cache/loader.py`) uses it to hydrate `CacheService` at startup and on periodic/manual sync. `ServiceDataModel` (`services/core/data/service.py`) is the higher-level façade combining cache + data service that endpoints and factories actually depend on.

**Tables:**
- `users` (tg_id, server_id, created_at, ref_count, is_admin)
- `keys` (email, tg_id, expiry_time, key, inbound_id, tariff_id, client_id, created_at)
- `tariffs` (id, name_tariff, amount, duration_months, traffic_gb, is_active)
- `payments` (payment_id, tg_id, amount, status, payment_type, created_at, updated_at)
- `servers` (id, url, api_url, availability)
- `stocks` (id, name, amount, description, created_at)
- `api_clients` (public-API keys — see External API clients below)
- (and others — see the `models/` package, one subpackage per entity, e.g. `models/gifts`, `models/referrals`)

### 3x-UI Integration

**Client:** Native httpx client for 3x-ui v3.2.0 standalone API (`client.py`). The `py3xui` dependency has been removed.

**Auth modes:**
1. Bearer token (API Token from panel settings) — preferred, no CSRF.
2. Session cookie (CSRF + login flow).

**Key classes:**
- `_StandaloneClientAPI` — low-level httpx wrapper for `/panel/api/` endpoints.
- `XUISession` — high-level service with retry policy (`tenacity`), metrics, and auth state management.
- `PanelClient` — dataclass DTO replacing `py3xui.Client`.

**Operations:**
- `add_client()` — create VPN key (returns client_id)
- `update_client()` — modify key (traffic limit, expiry, etc.)
- `delete_client()` — remove key
- `get_inbound()` — fetch inbound config
- `get_client_traffic()` — fetch client traffic stats
- `list_clients_paged()` — raw paginated/filtered client list from panel (for reconciliation tooling, not a source of truth for status)

**Error Handling:** Retry logic via `tenacity` for network/temporal errors (ConnectionError, TimeoutError). Authentication errors are non-retryable. If 3x-UI is down after retries, key operations fail with 502.

### YooKassa Integration

**Payment Flow:**
1. Web/Bot calls `POST /payments/create` with `tg_id`, `tariff_id`, `operation` (create_key|renew_key)
2. Backend creates invoice via `yookassa.Payment.create()`
3. Response includes `confirmation_url` (redirect to payment page)
4. User pays, YooKassa POSTs webhook to `/payments/webhook`
5. Backend verifies IP + signature, updates payment status
6. If status == "succeeded", calls `KeyCreation.process()` to create/renew key

**Idempotency:** Webhook processing checks `status == "succeeded"` before creating key. Duplicate webhooks are ignored.

### Background Tasks

`background/scheduler.py` sets up APScheduler jobs:
- **Cache sync** (`_sync_cache`) — every 3 hours. Reloads all data from PostgreSQL into `CacheService`.
- **Panel sync** (`_sync_panel`) — every 3 hours. Syncs 3x-UI panel clients with DB+cache, cleans up orphaned keys, updates traffic stats.
- **Notifications** (`_run_notifications`) — every 1 hour. Runs notification funnels (key expiry, trial reminders, referral bonuses).

### Caching Strategy

**On Startup:**
- `LoadingService.load_all()` fetches all data from PostgreSQL
- CacheService stores: users, tariffs, keys, servers, stocks

**On Mutation:**
- Create key → add to cache
- Delete key → remove from cache + update user ref_count
- Update payment → update cache status
- Renew key → update expiry in cache

**Cache TTL:** Configurable per-entity (default: no TTL, refreshed only on mutation or periodic sync).

**Cache Invalidation:** `POST /admin/rebuild-cache` manually syncs from DB.

### Logging

Structured logging via `logger.py` (repo root) — a loguru-backed `StructuredLogger` singleton, initialized once in `app/main.py` via `setup_logging(...)`. Import the shared singleton rather than creating a per-module logger:

```python
from logger import logger

logger.info("Key created", email="user@example.com", tg_id=123)
logger.warning("3x-UI unavailable", error=str(e))
logger.error("Payment webhook verification failed", reason="IP mismatch")
```

`logger.py` also masks sensitive kwargs (`_mask_sensitive`) and binds a per-request `trace_id` (`generate_trace_id`/`set_trace_id`, wired in `app/main.py`'s request middleware). A handful of modules instead use stdlib `logging.getLogger(__name__)` — prefer the shared `logger` singleton for new code.

Configurable via env vars:
- `LOG_LEVEL` — DEBUG, INFO, WARNING, ERROR (default: INFO)
- `LOG_FILE` — output file path (default: stdout)
- `LOG_FORMAT` — detailed, simple, json (default: detailed)

## Testing Patterns

Tests use `AsyncMock` for asyncpg, native XUI client, and yookassa clients.

**Setup Pattern:**
```python
@pytest.fixture
async def mock_service_data():
    # Mock ServiceDataModel with all sub-services
    service_data = MagicMock(spec=ServiceDataModel)
    service_data.keys = AsyncMock()
    service_data.users = AsyncMock()
    service_data.tariffs = AsyncMock()
    # ... etc
    return service_data

@pytest.fixture
async def mock_pool():
    return AsyncMock(spec=asyncpg.Pool)

# In test:
app.dependency_overrides[get_service_data] = lambda: mock_service_data
app.dependency_overrides[get_pool] = lambda: mock_pool
```

**Example Test:**
```python
@pytest.mark.asyncio
async def test_create_key_free_tariff(client, mock_service_data, mock_pool):
    # Setup mocks
    mock_service_data.tariffs.get_data.return_value = Mock(amount=0, id=1)
    mock_service_data.users.get_data.return_value = Mock(tg_id=123, server_id=1)
    
    # Call endpoint
    response = client.post(
        "/api/v1/keys/create",
        json={"tg_id": 123, "tariff_id": 1},
        headers={"X-Bot-Secret": "test_secret"},
    )
    
    # Assert
    assert response.status_code == 200
    assert "email" in response.json()
```

## Factories

**`build_key_services(pool, service_data, cache, data_service)`:**
- Returns `(create_key, key_renewal, xui)` tuple
- Initializes: ExpiryCalculator, LoadingService, XUISession, FormConnectionData, FormationKey, CreateKey, KeyUpdater, KeyResetter, KeyRenewal
- Used by: payment webhook flow, direct key endpoints, payment router

**`build_payment_router(pool, service_data, cache, data_service)`:**
- Returns `PaymentRouter` instance
- Calls `build_key_services()` internally
- Initializes: PaymentProcessor, KeyCreationService, KeyRenewalService

## Environment Variables

All settings are defined in `config.py` (`Settings`, pydantic-settings, loads `.env` at repo root) — that file is the source of truth for names/defaults/aliases; a snapshot with dummy values is at `.env.example`. Highlights:
- `DATABASE_URL` — asyncpg DSN
- `BOT_SECRET_KEY` / `ADMIN_API_KEY` / `INVITE_TOKEN` / `MVP_APP_SECRET` — all rejected at startup if left at insecure/placeholder defaults (`shared.config.core.reject_insecure_secret`)
- `XUI_API_URL` / `XUI_LOGIN` / `XUI_PASSWORD` — 3x-UI panel credentials
- `AVAILABLE_CONNECTIONS` — JSON/list of panel inbound IDs allowed for new keys (used by `FormConnectionData`; panel inbounds are filtered by this)
- `XUI_INBOUND_ID_LANDING` — fixed panel inbound ID for landing keys (Telegram-only baseline) and for the separate anonymous landing-page flow (`/api/v1/landing`). Paid keys are created on `[XUI_INBOUND_ID_LANDING] + AVAILABLE_CONNECTIONS` and stay there for the life of the key — the 3x-ui panel cuts VPN access on its own once the client's `expiryTime` passes, no code-driven inbound detach. The panel client is **not** deleted automatically — physical deletion from the panel is admin-only (`admin_delete_key` / bulk "delete expired keys" in the bot admin panel).
- `DEFAULT_PRICING_PLAN` — default tariff ID for trial keys
- `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` — payment processing. `DISABLE_WEBHOOK_IP_CHECK` bypasses the YooKassa IP allowlist for local/dev — the allowlist itself is hardcoded in `api/v1/payments.py::_check_webhook_ip`, not env-configurable.
- `WEBHOOK_BASE_URL` — public URL for YooKassa callbacks (e.g., https://api.example.com)
- `ADMIN_ID` — JSON array of admin Telegram IDs
- `BOT_TOKEN` — for sending user notifications and (via `services/core/referral`) channel-subscription checks against `CANALL_URL`
- `LOG_LEVEL` / `LOG_FILE` / `LOG_FORMAT` — see Logging below
- `MVP_APP_SECRET` / `MVP_SHARED_KEY_EMAIL` — see Mobile MVP endpoint above (empty `MVP_SHARED_KEY_EMAIL` by default; endpoint returns 500 until set)
- `LANDING_COOKIE_SECRET` / `LANDING_PUBLIC_URL` / `CHANNEL_BONUS_DAYS` — landing-page and channel-subscription-bonus flow
- `PAYMENT_SWEEP_MAX_AGE_MINUTES` / `PAYMENT_SWEEP_EXCLUDE_IDS` — safety-net poller for missed YooKassa webhooks (see `background/scheduler.py`)
