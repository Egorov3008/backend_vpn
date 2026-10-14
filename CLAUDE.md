# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Backend API

FastAPI backend serving as the source of truth for VPN service business logic.

## Standalone deployment (extracted from the platform monorepo)

This repository was extracted from the `platform/` monorepo via `git filter-repo`,
keeping full commit history. The long-term direction is **full independence from the
monorepo**: `bot` and `web` move to their own servers and talk to `backend` over HTTP
only. Nothing outside this repo should touch the database.

- **Networking:** `docker-compose.yml` is self-contained. `postgres` and `backend`
  share the internal `backend_net` bridge network. No external networks, no
  `platform_default` dependency. Clients will reach `backend:8000` through a private
  channel (planned: WireGuard between servers); the port is not published publicly
  by compose.
- **Database:** `backend` owns the database. The stack's `postgres` service uses the
  new volume `backend_postgres_data`. **Only `backend` may connect to Postgres**, so
  the DB port is not published to the host. Clients (bot, web) must never get DB
  credentials; they go through the API. (History: an earlier revision ran two
  Postgres processes against the same `platform_postgres_data` volume and corrupted
  the WAL — do not point two Postgres processes at one data directory.) Schema DDL
  (`db/schema_fixed.sql`) initializes a fresh volume and is idempotent with the payment
  migrations. Use `tools/db/` for backup/restore/migration.
- **Migration from platform (pending, phase 3):** the existing `bot_db` data lives in
  `platform-postgres-1` (`platform_postgres_data`). Move it with `pg_dump` and
  `pg_restore` into `backend_postgres_data` during a write freeze, then verify row
  counts. The old volume stays untouched until the new one is verified.
- **`shared/`:** this repo has its own copy of `shared/`. `platform/bot/` keeps its own
  copy. **There is no automated sync** — when `shared/config/core.py` changes in one
  place, mirror it manually and review the diff. Do not `rsync --delete` blindly: the
  backend copy has `reject_insecure_secret` validation that the platform copy lacks.
- **Secrets:** `BOT_SECRET_KEY` and `ADMIN_API_KEY` are shared with every client that
  calls the API. Rotate them by hand in every place they are used. `DB_*` credentials
  are local to `backend` only.

## Commands

No venv is set up in the repo and `python` is not on PATH — use `python3` (or an activated venv).

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
pytest tests/api/test_keys.py::test_list_keys_single

# Integration tests (tests/integration/*_real_db.py) need a real Postgres:
# set TEST_DATABASE_URL; tools/db/init_test_db.py prepares the test database.
# Without it they are skipped.
TEST_DATABASE_URL=postgresql://... pytest tests/integration

# Fail-fast / coverage / one module (Makefile wrappers around pytest)
make test-fast
make test-cov            # HTML report at htmlcov/index.html
make test-module MODULE=api

# Lint / auto-fix + format
make lint                # NOT clean today: the repo has no ruff config, so ruff's default rules report ~900 findings
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
- Only for paid tariffs (free keys created directly via `POST /keys` endpoint)

**`PaymentProcessor` (`services/core/payment/processor.py`):**
- Validates YooKassa webhooks
- Updates payment status in database
- Calls KeyCreation on success

**`KeyRenewal` (`services/core/keys/utils/renewal.py`):**
- Extends key expiry in 3x-UI and database
- Resets notification flags and traffic counters
- Callable from `PATCH /keys/{email}` endpoint or payment flow

**`CacheService` (`services/cache/service.py`):**
- In-memory cache with TTL (loaded at startup)
- Holds users, tariffs, keys, servers, stocks
- Updated on every mutation (create/delete/renew key, update payment status)

### API Endpoints

Routes live in `api/v1/<resource>.py` — grep there for the full list. Behaviour that is easy to get wrong:

- **Keys (`/api/v1/keys`):** `POST /keys/` needs the trailing slash — `POST /api/v1/keys` 307-redirects, and POST clients don't follow it. Creating a key is free-tariff only (402 for paid; those go through payments). `POST /keys/trial` sets `user.trial = 1` and returns 403 if the trial was already used. `PATCH` and `DELETE /{email}` check that `tg_id` owns the key; `PATCH` rejects paid tariffs (402).
- **Payments (`/api/v1/payments`):** `GET /{payment_id}/status` never processes a payment (never calls `route()`). For a `pending` payment it asks YooKassa at most once per 30 s (15 s timeout) and only does `pending → canceled` via a conditional `UPDATE ... WHERE status='pending'`. `POST /create` sets `payment_type` to `create_key|renew_key`. `POST /webhook` is YooKassa-only (IP allowlist, see YooKassa Integration).
- **Users / tariffs:** plain reads; `POST /users/` is auto-called by bot/web on first key creation.
- **Auth (`/api/v1/auth`):** `POST /register-from-invite` (checked against `INVITE_TOKEN`) and `POST /telegram-login` (`app/core/telegram.py::verify_telegram_hash`).

#### Landing (`/api/v1/landing`)

Anonymous, cookie-based flow for the marketing landing page (separate from the bot/web `tg_id`-trusting flow above): `POST /set-ref-cookie`, `POST /quick-key` (mints a short-lived key on `XUI_INBOUND_ID_LANDING`), `GET /state`, `POST /mark-converted/{landing_uid}`, `POST /claim/{landing_uid}`. Signed cookies use `LANDING_COOKIE_SECRET` (falls back to `BOT_SECRET_KEY`).

`mark-converted` and `claim` are bot-only: they also require `X-Landing-Internal-Secret: <LANDING_INTERNAL_SECRET>` (`app/auth.py::verify_landing_internal_secret`, fail-closed when unset). nginx injects `X-Bot-Secret` on public `/api/v1/landing/*`, so that header alone must not open these routes.

#### Mobile MVP (`/api/v1/mobile`)

- **GET `/shared-config`** — One shared VPN config for the MVP Android app; no per-user auth, no accounts, no per-user state.
  - Auth: `X-App-Secret: <MVP_APP_SECRET>` header (checked by `verify_app_secret()`, **not** `verify_bot_secret()` — see Authentication exception below).
  - Resolves the single key at `MVP_SHARED_KEY_EMAIL`, downloads/parses its subscription URL (with an in-memory 5-minute TTL cache, since every caller gets the identical response), and returns `{vless_uri, expiry_time}`.
  - Fails: 500 if the shared key isn't configured/found (deploy/provisioning error); 502 if the upstream subscription download/parse fails.
  - Provisioning (one-time, manual): create a free tariff with `amount=0` and `limit_ip=0` (ideally a long `period`), call `POST /keys` once against it to mint the shared key, then paste that key's `email` into `MVP_SHARED_KEY_EMAIL`.

#### App distribution (`/api/v1/public/app`, `api/v1/app_distribution.py`)

- **GET `/android`** — redirects (302) to the current Android APK (`dlay.svoih`, `../android_app`) at a stable public URL — no auth (meant to be shared directly via landing/QR/chat), only per-IP rate limiting (`app/rate_limit.py`).
- The APK itself is **not** built or uploaded by this backend — it's placed manually on the host at `static/downloads/dlay-svoih.apk` (bind-mounted into the container via `docker-compose.yml`, so a new release only needs the file replaced on the host, not an image rebuild) after running `./gradlew assembleRelease` in `android_app/`. Overwriting that file is the entire "release" process; there is no versioning/history — the redirect always points at whatever file is currently there.

#### Admin (`/api/v1/admin`, `api/v1/admin.py`)

Split into two routers with different auth (see Authentication below):

- **`router`** (read-only + `verify_admin_or_bot`, i.e. bot secret also accepted): stats, scheduler status, maintenance-mode status, user/key/payment listings, gift/tariff/referral lookups.
- **`destructive_router`** (`verify_admin_actor` only — `X-API-Key` + `X-Admin-Tg-Id`): mutating ops — `PUT maintenance-mode`, `DELETE users/{tg_id}`, `POST keys` (generate), `POST keys/renewals` (mass-renew), `PATCH keys/{email}/expiry`, `PATCH keys/{email}/tariff`, `DELETE users/inactive`, `POST sync-jobs` + `GET sync-jobs/{job_id}`, and `api-clients` create/list/`DELETE {id}` (revoke)/`POST {id}/keys` (rotate).

Grep `api/v1/admin.py` for the current full route list rather than trusting a manually maintained enumeration here — it grows frequently (referrals, gift codes, promotions, and sync jobs were all added after this section was first written).

**Admin panel (`admin_panel/`):** vanilla-JS SPA (no build step) consuming `/api/v1/admin/*`, served by FastAPI itself via `StaticFiles` mounted at `/admin-panel` (`app/main.py`). Hash-based routing (`#/users`, `#/keys`, …) since `StaticFiles` doesn't rewrite sub-paths to `index.html`. Auth is the `ADMIN_API_KEY` + Telegram ID entered at `#/login`, stored client-side and sent as `X-API-Key`/`X-Admin-Tg-Id`. `pytest` only checks the static mount doesn't break other routes — it can't verify client-side behavior, so any change here must also be driven through a real browser (see `admin_panel/README.md` for the manual checklist: bad-key error, login redirect, paginated `#/users`, one destructive round-trip via maintenance-mode toggle, corrupted-key auto-logout, no console errors). Mass mailing/broadcast (present in the bot's own admin panel) has no backend endpoint and is intentionally out of scope — the nav shows a disabled entry pointing back to the bot.

### Authentication

**Service-to-Service:** `X-Bot-Secret: <BOT_SECRET_KEY>` header required on all endpoints (checked by `verify_bot_secret()` dependency), **except** `/api/v1/mobile/shared-config`, which uses its own static secret (`X-App-Secret: <MVP_APP_SECRET>`, checked by `verify_app_secret()`) — see Mobile MVP above.

**No user authentication** — backend trusts the `tg_id` parameter from the calling service (bot or web). The calling service is responsible for JWT validation.

**Telegram Mini App:** user sessions, not service secrets. `POST /api/v1/miniapp/auth` verifies `Telegram.WebApp.initData` (`app/core/telegram.py::verify_webapp_init_data`) and returns a signed token (`app/core/miniapp_session.py`; HMAC key `MINIAPP_SESSION_SECRET`, falls back to `BOT_SECRET_KEY`). Every `/api/v1/miniapp/*` route takes the user from that token via `verify_miniapp_session`, never from a `tg_id` in the request; renewals check ownership with `_require_own_key`.

**External API clients (public-api tag):** Unlike bot/web/mobile-mvp above, these don't use a static env-secret — `Authorization: Bearer <key>` against per-client keys stored (hashed) in the `api_clients` table, checked by `verify_api_client(required_scopes=[...])` (`app/auth.py`, `services/api_clients/service.py`). Keys are issued/listed via admin-only `POST/GET /admin/api-clients`, revoked via `DELETE /admin/api-clients/{id}`, and rotated via `POST /admin/api-clients/{id}/keys` (`X-API-Key` + `X-Admin-Tg-Id`, same as other destructive admin ops), optionally passing `allowed_domain` at creation — if set, `verify_api_client` rejects requests whose `Origin`/`Referer` host doesn't match it (unset = unrestricted, e.g. existing/server-to-server keys). The raw key is only ever shown once, in the create/rotate response.

All public endpoints live under `api/v1/public/` (a package: `tariffs.py`, `keys.py`, `payments.py`, `users.py`, `auth.py`), one file per internal resource — each re-registers the *same* handler functions from the corresponding internal `api/v1/*.py` router via `router.add_api_route(...)`, just with `verify_api_client(required_scopes=[...])` instead of `verify_bot_secret`, so business logic isn't duplicated. Scopes are per-resource, split read/write (`tariffs:read`, `keys:read`/`keys:write`, `payments:read`/`payments:write`, `users:read`/`users:write`, `auth:register`). `payments.py`'s webhook is deliberately excluded — it stays YooKassa-only (IP allowlist), never gets a public counterpart. `app/main.py`'s `public_api_cors_middleware` adds CORS headers (reflecting `Origin`) only for paths under `/api/v1/public/`, so browser-based callers can complete preflight — actual origin enforcement is the `allowed_domain` check above, not CORS.

Two independent nginx edges proxy `/api/v1/public/` straight to `backend` (not through `web`) on this shared host — **only this prefix is reachable from outside**, so a route only becomes truly public by living under `api/v1/public/`, regardless of its auth — both rate-limiting at `limit_req zone=public_api` (60r/m, burst 20) in addition to in-app (`app/rate_limit.py`): the platform monorepo's own `nginx/default.conf.template`, and `/home/admin/landing-pilot/nginx/default.conf.template` (a separate repo — see its CLAUDE.md) which fronts `tolko-dlya-svoih.ru` and already held ports 443/8443 on this box before `backend` needed public exposure, so that's the one to extend for this domain rather than standing up a third edge.

### Database

**Ownership:** `backend` owns the database. Its `postgres` service (in `docker-compose.yml`, volume `backend_postgres_data`) is the only access point. Clients use the HTTP API, never the DB directly. See the Database bullet under Standalone deployment above.

**Connection Pool:** asyncpg, created once at startup by `create_db_pool()` (`database/base.py`) and stored on `app.state.pool`. Injected per-endpoint via `Depends(get_pool)` (`app/dependencies.py`; also `get_cache` and `get_service_data` for the other two `app.state` singletons set up in `app/main.py`'s lifespan).

**`DataService`** (`database/service.py`) is the raw asyncpg query layer per entity; `LoadingService` (`services/cache/loader.py`) uses it to hydrate `CacheService` at startup and on periodic/manual sync. `ServiceDataModel` (`services/core/data/service.py`) is the higher-level façade combining cache + data service that endpoints and factories actually depend on.

**Tables:**
- `users` (tg_id, server_id, created_at, ref_count, is_admin)
- `keys` (email, tg_id, expiry_time, key, inbound_id, tariff_id, client_id, created_at)
- `tariffs` (id, name_tariff, amount, duration_months, traffic_gb, is_active)
- `payments` (payment_id, tg_id, amount, status, payment_type, created_at, updated_at, processing_started_at, processing_token, succeeded_at, renewal_expiry_time)
- `keys.payment_id` — payment that issued the key (NULL for trial/admin/landing-free keys), unique when set
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
5. Backend verifies IP + signature, then runs `PaymentRouter.route(payment_id)`
6. `route()` takes an atomic claim (`pending → processing`, owner token in `payments.processing_token`) and only then calls `KeyCreation.process()` / `KeyRenewal`. On success, `complete_claim` sets `succeeded`; on failure, `release_claim` returns the payment to `pending` for retry

**Idempotency:** four layers.
- The claim (`PaymentRepository.claim_pending`) lets only one of webhook / sweep process a payment (`GET /status` never processes, it only cancels). A `processing` claim older than `PAYMENT_CLAIM_STALE_MINUTES` (10) can be taken over; the previous owner can no longer complete or release it.
- `keys.payment_id` has a partial unique index (`uq_keys_payment_id`), so a second key for the same payment cannot be written. `KeyCreationService` also returns the existing key (`already_created`) before any landing upgrade.
- Only the claim owner sends the user notification.
- **Renewals:** the target expiry is computed once and stored in `payments.renewal_expiry_time` (`PaymentRepository.reserve_renewal_expiry`, conditional write, `IS NULL`) before the panel is touched (`KeyRenewal.extension_key(expiry_guard=...)`). A retry after a DB failure or a takeover reuses the stored value, so the panel and DB get the same absolute expiry and the period is not added twice. Migration `db/migrations/20261014_payment_renewal_expiry.sql`. Direct renewals without a payment (`PATCH /keys/{email}`) are unchanged.

**Schema migrations** for the claim: `db/migrations/20261011_*`, `20261012_*`, for success timestamp `20261013_*` (`payments.succeeded_at`), and for renewal expiry `20261014_*`. Apply by hand before deploying code that uses them (see the header of each file).

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

Tests use `AsyncMock` for asyncpg, the native XUI client, and yookassa clients. Async tests run in auto mode (`pytest.ini`).

**Shared fixtures are in `tests/api/conftest.py`:** `mock_service_data` and `api_client` (an httpx `AsyncClient` over `ASGITransport`). `api_client` overrides `get_pool`, `get_cache`, `get_service_data` and every auth dependency (`verify_bot_secret`, `verify_api_key`, `verify_admin_or_bot`, `verify_admin_actor`, `verify_landing_internal_secret`) with no-ops, and clears `app.dependency_overrides` afterwards. To test a real auth check, pop its override inside the test (see `real_internal_secret_check` in `tests/api/test_landing.py`).

Mocking gotchas seen in the suite:
- Landing keys are looked up through `service_data.cache_service.keys.all()`, not `keys.get_data()` — mock the path the handler actually uses.
- `pool.acquire()` used as `async with` needs `__aenter__`/`__aexit__` set on the mock (`AsyncMock()` alone is not enough).

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
- `LANDING_INTERNAL_SECRET` — required header value for landing `claim` / `mark-converted`; empty = those routes are closed (see Landing)
- `MINIAPP_SESSION_SECRET` / `MINIAPP_SESSION_TTL_SECONDS` — Mini App session tokens (see Authentication)
- `PAYMENT_SWEEP_MAX_AGE_MINUTES` / `PAYMENT_SWEEP_EXCLUDE_IDS` — safety-net poller for missed YooKassa webhooks (see `background/scheduler.py`)
