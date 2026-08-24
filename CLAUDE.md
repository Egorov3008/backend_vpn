# Backend API

FastAPI backend serving as the source of truth for VPN service business logic.

## Standalone deployment (extracted from the platform monorepo)

This repository was extracted from the `platform/` monorepo (which still hosts `bot/`
and `web/`) via `git filter-repo`, keeping full commit history. It is a fully
independent service: own Dockerfile, own `docker-compose.yml`/`docker-compose.dev.yml`,
own CI (`.github/workflows/ci.yml`), own Postgres.

- **Networking:** `docker-compose.yml` attaches `backend` to two networks — an internal
  `db_net` (backend ↔ its own `postgres`) and the platform's existing external network
  `platform_net` (real name: `platform_default`). The external network is what lets
  `bot`/`web`/`nginx` in `platform/` keep resolving `backend:8000` by service name,
  unchanged, even though it's now a different compose project.
- **Database:** Postgres moved into this repository — it's the only service that talks
  to the DB directly (bot/web only ever go through this API). Production data was moved
  via zero-copy Docker volume reuse (`platform_postgres_data`, declared `external: true`
  in `docker-compose.yml`), not a dump/restore copy. Schema DDL lives at
  `db/schema_fixed.sql` (only used to init a genuinely empty volume — a no-op against the
  reused production volume). DB backup/restore/migration tooling lives in `tools/db/`.
- **`shared/`:** this repo has its own copy of `shared/` (was a bind-mount in the
  monorepo). `platform/bot/` keeps its own separate copy. **There is no automated sync**
  — when `shared/config/core.py` changes in one place, mirror it manually:
  `rsync -a --delete platform/shared/ platform-backend/shared/`, review the diff, run
  tests before committing on both sides.
- **Secrets:** `BOT_SECRET_KEY` and `ADMIN_API_KEY` must match the values in
  `platform/.env` (read by `bot`/`web`) — no automated sync, rotate by hand in both
  places. `DATABASE_URL`/`DB_*` are no longer shared with the platform monorepo.

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

#### Mobile MVP (`/api/v1/mobile`)

- **GET `/shared-config`** — One shared VPN config for the MVP Android app; no per-user auth, no accounts, no per-user state.
  - Auth: `X-App-Secret: <MVP_APP_SECRET>` header (checked by `verify_app_secret()`, **not** `verify_bot_secret()` — see Authentication exception below).
  - Resolves the single key at `MVP_SHARED_KEY_EMAIL`, downloads/parses its subscription URL (with an in-memory 5-minute TTL cache, since every caller gets the identical response), and returns `{vless_uri, expiry_time}`.
  - Fails: 500 if the shared key isn't configured/found (deploy/provisioning error); 502 if the upstream subscription download/parse fails.
  - Provisioning (one-time, manual): create a free tariff with `amount=0` and `limit_ip=0` (ideally a long `period`), call `POST /keys/create` once against it to mint the shared key, then paste that key's `email` into `MVP_SHARED_KEY_EMAIL`.

#### Admin (`/api/v1/admin`)

- **GET `/health`** — System health check
- **POST `/rebuild-cache`** — Force cache refresh from database
- **GET `/stats`** — Dashboard stats (total users, key stats)
- **GET `/users`** — List all users
- **GET `/users/{tg_id}`** — Get user details
- **GET `/users/{tg_id}/stock`** — Get active discount/stock for user
- **POST `/users/register`** — Register new user (called by bot)
- **POST `/users/{tg_id}/keys/generate`** — Admin generate key for user
- **POST `/users/{tg_id}/keys/mass-renew`** — Mass renewal for user's keys
- **POST `/keys/{email}/change-date`** — Change key expiry date
- **POST `/keys/{email}/change-tariff`** — Change key tariff
- **POST `/keys/{email}/reset-traffic`** — Reset key traffic counters

### Authentication

**Service-to-Service:** `X-Bot-Secret: <BOT_SECRET_KEY>` header required on all endpoints (checked by `verify_bot_secret()` dependency), **except** `/api/v1/mobile/shared-config`, which uses its own static secret (`X-App-Secret: <MVP_APP_SECRET>`, checked by `verify_app_secret()`) — see Mobile MVP above.

**No user authentication** — backend trusts the `tg_id` parameter from the calling service (bot or web). The calling service is responsible for JWT validation.

### Database

**Connection Pool:** asyncpg (`app/core/database.py`). Injected per-endpoint via `Depends(get_pool)`.

**Tables:**
- `users` (tg_id, server_id, created_at, ref_count, is_admin)
- `keys` (email, tg_id, expiry_time, key, inbound_id, tariff_id, client_id, created_at)
- `tariffs` (id, name_tariff, amount, duration_months, traffic_gb, is_active)
- `payments` (payment_id, tg_id, amount, status, payment_type, created_at, updated_at)
- `servers` (id, url, api_url, availability)
- `stocks` (id, name, amount, description, created_at)
- (and others — see `models.py`)

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

Structured logging via `app/core/logging.py`. Use `get_logger(__name__)` in every module.

```python
from app.core.logging import get_logger
logger = get_logger(__name__)

logger.info("Key created", email="user@example.com", tg_id=123)
logger.warning("3x-UI unavailable", error=str(e))
logger.error("Payment webhook verification failed", reason="IP mismatch")
```

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

Required in `.env`:
- `DATABASE_URL` — asyncpg DSN
- `BOT_SECRET_KEY` — shared secret with bot/web clients
- `TELEGRAM_BOT_TOKEN` — for sending user notifications
- `XUI_API_URL` / `XUI_LOGIN` / `XUI_PASSWORD` — 3x-UI panel credentials
- `AVAILABLE_CONNECTIONS` — JSON/list of panel inbound IDs allowed for new keys (used by `FormConnectionData`; panel inbounds are filtered by this)
- `XUI_INBOUND_ID_LANDING` — fixed panel inbound ID for landing keys (Telegram-only baseline). Paid keys are created on `[XUI_INBOUND_ID_LANDING] + AVAILABLE_CONNECTIONS` and stay there for the life of the key — the 3x-ui panel cuts VPN access on its own once the client's `expiryTime` passes, no code-driven inbound detach. The panel client is **not** deleted automatically — physical deletion from the panel is admin-only (`admin_delete_key` / bulk "delete expired keys" in the bot admin panel).
- `DEFAULT_PRICING_PLAN` — default tariff ID for trial keys
- `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` — payment processing
- `WEBHOOK_BASE_URL` — public URL for YooKassa callbacks (e.g., https://api.example.com)
- `WEBHOOK_ALLOWED_IPS` — comma-separated IPs (YooKassa: 185.71.76.0/27,185.109.44.0/27)
- `ADMIN_TG_IDS` — JSON array of admin Telegram IDs
- `LOG_LEVEL` — logging level (default: INFO)
- `MVP_APP_SECRET` — static shared secret for the MVP Android app's `X-App-Secret` header (see Mobile MVP endpoint above)
- `MVP_SHARED_KEY_EMAIL` — email of the pre-provisioned shared key served by `/api/v1/mobile/shared-config` (empty by default; endpoint returns 500 until set)
