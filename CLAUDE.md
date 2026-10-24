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
  Clients on the same host (the `platform-web` nginx) join the fixed-name network
  `backend_api` as an external network and reach `backend:8000` there; `postgres` is
  not on `backend_api`, so a client container cannot open a DB connection at all.
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

#### Keys (`/api/v1/keys`)

- **GET `/?tg_id=...`** — List user's keys (`limit`/`offset` optional, total count in `X-Total-Count` header)
- **GET `/{email}`** — Get key details (includes client_id, key config, expiry, trial status)
- **POST `/`** — Create new key (free tariffs only). Trailing slash is mandatory — `POST /api/v1/keys` (no slash) 307-redirects to `POST /api/v1/keys/`, and `httpx`/most HTTP clients don't follow redirects on POST by default.
  - Requires: `tg_id`, `tariff_id`
  - Fails: 402 if tariff is paid (use payments flow instead)
- **POST `/trial`** — Create a free trial key (sets `user.trial = 1`)
  - Requires: `tg_id` (query param)
  - Optional: `gift_token` (query param) — applies gift if provided
  - Fails: 403 if trial already used
- **PATCH `/{email}`** — Renew key expiry
  - Requires: `tg_id`, `tariff_id`, `number_of_months`
  - Fails: 402 if tariff is paid
- **DELETE `/{email}`** — Delete key (from 3x-UI and DB)
  - Requires: `tg_id` (ownership check)

#### Payments (`/api/v1/payments`)

- **GET `/?tg_id=...`** — Payment history (`limit`/`offset` optional, total count in `X-Total-Count` header)
- **GET `/{payment_id}/status`** — Check payment status
  - Requires: `tg_id` (ownership check)
  - Never processes the payment (never calls `route()`). If the stored status is `pending`, it asks YooKassa (at most once per 30s per payment, 15s timeout) and only handles cancellation: `pending → canceled` via a conditional `UPDATE ... WHERE status='pending'`.
- **POST `/quotes`** — Calculate payment amount (discounts breakdown) without creating a payment
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

`mark-converted` and `claim` are bot-only: they also require `X-Landing-Internal-Secret: <LANDING_INTERNAL_SECRET>` (`app/auth.py::verify_landing_internal_secret`, fail-closed when unset). nginx injects `X-Bot-Secret` on public `/api/v1/landing/*`, so that header alone must not open these routes.

#### Mini App and web cabinet (`/api/v1/miniapp`, `/api/v1/web`)

Both are built by one factory, `api/v1/cabinet.py::build_cabinet_router`, which reuses the internal handlers (keys, payments, tariffs, referral link). The factory takes only the auth dependency and the YooKassa `return_url`; `tg_id` always comes from the session, never from the request.

- **Mini App:** `POST /miniapp/auth` verifies `Telegram.WebApp.initData` and returns a Bearer token (`verify_miniapp_session`).
- **Web:** for users who may have no Telegram access yet. Session in HttpOnly cookie `web_session` (`SameSite=Strict`, `Path=/api/v1/web`); mutating requests also need `X-Requested-With: web` (`app/auth.py::verify_web_session`). Both token kinds are signed with `MINIAPP_SESSION_SECRET` under different labels (`app/core/session_tokens.py`), so one is never accepted as the other.
  - `POST /web/anonymous/key`, `GET /web/anonymous/state` — the landing 24h key (`api/v1/landing.py` functions), no account.
  - `GET /web/anonymous/tariffs` — paid tariffs from `AVAILABLE_RATES` for the landing, no session. `POST /web/anonymous/ref` — a guest who came with `?ref=` gets the signed `tg_ref` cookie (`Path=/api/v1/web`); `POST /web/auth/bot-login/complete` attaches the referral via `landing._attach_referral_if_any` (same anti-fraud rules) and only then clears the cookie. A failed attach never blocks login.
  - `POST /web/auth/bot-login` → deep link `t.me/<bot>?start=weblogin_<token>` + poll secret in cookie `web_login_poll`; the bot asks the user to confirm and calls `POST /auth/web-login/{token}/confirm` (`X-Bot-Secret` + `X-Landing-Internal-Secret`), which returns the browser's anonymous `landing_uid` so the bot can attach that key like `/start landing_<uid>`; the browser polls `POST /web/auth/bot-login/complete` and gets `web_session` once. Tokens live in `web_login_tokens` (hashes only, migration `db/migrations/20261015_web_login_tokens.sql`), logic in `services/web_auth/`.
  - Payments from the web return to `WEB_PUBLIC_URL/pay/result`.
  - Referral links go to the site only: `share_url` = `WEB_PUBLIC_URL/?ref=<token>` (`services/core/referral/link_generator.py::referral_share_url`), returned by `POST /miniapp|web/referral-link` and admin `/referrals/links*`. Clients (bot, Mini App, web) show it and never build it; there are no `t.me/<bot>?start=ref_…` links any more.
  - The web nginx must proxy only `/api/v1/web/` and inject no secrets; `/api/v1/auth/*` must never be public.

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
- **`destructive_router`** (`verify_admin_actor` — `X-API-Key` + `X-Admin-Tg-Id`, or a web admin session; never the bot secret): mutating ops — `PUT maintenance-mode`, `DELETE users/{tg_id}`, `POST keys` (generate), `POST keys/renewals` (mass-renew), `PATCH keys/{email}/expiry`, `PATCH keys/{email}/tariff`, `DELETE users/inactive`, `POST sync-jobs` + `GET sync-jobs/{job_id}`, and `api-clients` create/list/`DELETE {id}` (revoke)/`POST {id}/keys` (rotate).

**Web admin login (`api/v1/admin_session.py`):** `POST /api/v1/admin/session` takes `Telegram.WebApp.initData` of a separate admin bot, verifies it with `ADMIN_BOT_TOKEN` (max age `ADMIN_INIT_DATA_MAX_AGE_SECONDS`), requires `tg_id ∈ ADMIN_ID`, and returns a Bearer token (`admin_sessions`, label `admin-session-v1`, TTL `ADMIN_SESSION_TTL_SECONDS` = 8h). Bearer instead of a cookie because in Telegram Web the Mini App runs in a cross-site iframe where SameSite cookies are not sent. Both admin routers accept it: `app/auth.py` resolves the admin through `ADMIN_AUTHENTICATORS` (`ApiKeyAdminAuth` for the bot, `SessionAdminAuth` for the web; add a new login method as a new strategy). With a session, `admin_tg_id` for the audit comes from the token (the `X-Admin-Tg-Id` header is ignored) and `ADMIN_ID` is re-checked on every request. Logins are audited as `admin_login`.

**Month stats (`GET /admin/stats/month?month=YYYY-MM&tz=<IANA>`, `services/admin_stats/`):** charts of the web admin. Month vs the same days of the previous month and the whole previous month: succeeded payments, new Telegram users (`tg_id > 0`) and their payments, site signups (keys with `landing_uid`: linked Telegram = `converted_tg_id`, paid after the key), top tariffs by payments (renewals use the key's current tariff); plus active users/keys right now. Day boundaries use `tz`. One section class per block (`sections.py`); add a block by adding a section.

**List filters:** `GET /admin/users?q=` (part of tg_id, or username/name), `GET /admin/keys?tg_id=&q=&status=active|expiring|expired` (expiring = ≤3 days), `GET /admin/payments?tg_id=&status=`; filtering happens before `limit`/`offset` and `X-Total-Count` counts the filtered list (`services/admin_filters.py`).

Grep `api/v1/admin.py` for the current full route list rather than trusting a manually maintained enumeration here — it grows frequently (referrals, gift codes, promotions, and sync jobs were all added after this section was first written).

**Admin UI:** lives in `platform-web` (`/admin`, the Mini App of the admin bot, served on its own host) and uses the session login above. The old vanilla-JS `admin_panel/` mounted at `/admin-panel` was removed after the new admin went live (2026-10-16). Mass mailing stays in the bot.

### Authentication

**Service-to-Service:** `X-Bot-Secret: <BOT_SECRET_KEY>` header required on all endpoints (checked by `verify_bot_secret()` dependency), **except** `/api/v1/mobile/shared-config`, which uses its own static secret (`X-App-Secret: <MVP_APP_SECRET>`, checked by `verify_app_secret()`) — see Mobile MVP above.

**No user authentication** — backend trusts the `tg_id` parameter from the calling service (bot or web). The calling service is responsible for JWT validation.

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
- The claim (`PaymentRepository.claim_pending`) lets only one of webhook / manual sweep script process a payment (`GET /status` never processes, it only cancels). A `processing` claim older than `PAYMENT_CLAIM_STALE_MINUTES` (10) can be taken over; the previous owner can no longer complete or release it.
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

A test must catch broken behavior, not pin the implementation: check the result (rows in the DB,
the client in the panel, the HTTP answer), not which mocks were called. No checks of exact texts
or magic numbers.

**Endpoints → integration tests on a test DB** (`tests/integration/`, see its `conftest.py`):

- `TEST_DATABASE_URL` points at any Postgres where the user may `CREATE SCHEMA`. The session gets
  its own schema `test_<hex>` (`db/schema_fixed.sql` + all `db/migrations/`), tables are truncated
  before each test, the schema is dropped at the end. Without the variable the folder is skipped.
  CI sets it to the job's Postgres service.
- External services are the e2e fakes (`e2e/fakes.py`) started in a thread: 3x-UI, YooKassa (the
  SDK points there) and Telegram (httpx calls to api.telegram.org are redirected).
- The `backend` fixture gives the real app with real pool/cache/`ServiceDataModel`:
  `backend.client` (httpx, bot secret set), `backend.pool`, `backend.fakes` (`panel_client(email)`,
  `set_payment_status`, `fail_panel(...)`, `state()`), `backend.reload_cache()` after writing to the
  DB directly. Helpers in `tests/integration/helpers.py`.

```python
async def test_user_deletes_own_key(backend):
    key = await issue_trial(backend, 1006)

    resp = await backend.client.delete(f"/api/v1/keys/{key['email']}", params={"tg_id": 1006})

    assert resp.status_code == 204
    assert await db_key(backend, key["email"]) is None
    assert backend.fakes.panel_client(key["email"]) is None
```

```bash
TEST_DATABASE_URL=postgresql://user@127.0.0.1:5432/db pytest tests/integration
```

Unit tests stay for cheap pure logic (price/discount math, cookie signing, filters). Older
`tests/api/` files still use `MagicMock` `ServiceDataModel` via `app.dependency_overrides`; move
them to `tests/integration/` when touching them.

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
- `ADMIN_PAYMENT_NOTIFY_ENABLED` (default `true`) — after every successful payment (`PaymentRouter.route`, after `succeeded` is committed) each admin from `ADMIN_ID` gets a Telegram message via `BOT_TOKEN`: service = YooKassa payment `description` + payment ID, and the amount (`services/core/payment/admin_notification.py`). A failed send is logged and never breaks payment processing.
- `TRUSTED_PROXY_CIDRS` — proxies whose `X-Forwarded-For`/`X-Real-IP` are trusted by `app/client_ip.py` (rate limit, YooKassa webhook IP check). The XFF chain is walked right to left and the first non-proxy address wins; a direct client's headers are ignored. Default: loopback + private ranges.
- No hardcoded domains or bot usernames: the bot username comes from `getMe` on `BOT_TOKEN` (`BOT_NAME` is only a fallback, empty by default).
- `BOT_TOKEN` — for sending user notifications and (via `services/core/referral`) channel-subscription checks against `CANALL_URL`
- `LOG_LEVEL` / `LOG_FILE` / `LOG_FORMAT` — see Logging below
- `MVP_APP_SECRET` / `MVP_SHARED_KEY_EMAIL` — see Mobile MVP endpoint above (empty `MVP_SHARED_KEY_EMAIL` by default; endpoint returns 500 until set)
- `MINIAPP_URL` — public https URL of the Mini App (no `#`, validated at startup). When set, «🔄 Продлить ключ» in expiry notifications (24h, 10h, grace) is a `web_app` button to `?screen=pay&renew=<email>` (`services/notifications/miniapp_buttons.py`); empty keeps the bot callback `renew_key|<email>`.
- `LANDING_COOKIE_SECRET` / `LANDING_PUBLIC_URL` / `CHANNEL_BONUS_DAYS` — landing-page and channel-subscription-bonus flow
- `PAYMENT_SWEEP_MAX_AGE_MINUTES` / `PAYMENT_SWEEP_EXCLUDE_IDS` — window of fresh pending payments (used by `create_payment` to find an open invoice for the same purchase) and defaults for the manual `scripts/sweep_pending_payments.py`. There is no background poller: keys are issued only from the YooKassa webhook; a missed webhook is recovered with that script.
