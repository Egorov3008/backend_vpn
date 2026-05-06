# Тестирование Backend в Swagger

Swagger (OpenAPI) интегрирован в FastAPI и позволяет интерактивно тестировать API endpoints без написания клиентского кода.

## Доступ к Swagger UI

### Локальная разработка

1. Запустите backend:
```bash
cd backend && uvicorn app.main:app --reload
```

2. Откройте Swagger в браузере:
```
http://localhost:8000/docs
```

### Альтернативный UI (ReDoc)
```
http://localhost:8000/redoc
```

## Структура Swagger для VPN Platform

Swagger автоматически генерируется из кода FastAPI и отображает:

- **Все endpoints** из `/api/v1/` (keys, payments, tariffs, users, admin)
- **Параметры** (path, query, body)
- **Схемы** (models с описанием полей)
- **Аутентификация** (X-Bot-Secret header)
- **HTTP методы** (GET, POST, DELETE)

## Тестирование Endpoints в Swagger

### Шаг 1: Выбрать endpoint

Нажмите на любой endpoint, чтобы развернуть его детали:

```
GET /api/v1/keys/
```

Разворачивается форма со:
- **Parameters** — параметры запроса (query, path)
- **Request body** — JSON для POST запросов
- **Schema** — описание моделей и их полей
- **Try it out** — кнопка для выполнения запроса

### Шаг 2: Заполнить параметры

#### Пример: Получить список ключей пользователя

1. Нажмите на `GET /api/v1/keys/` → **Try it out**
2. В поле `tg_id` введите Telegram ID (например, `123456789`)
3. Нажмите **Execute**

Swagger отправит запрос и покажет:
- **Request URL** — полный URL запроса
- **Request body** — отправленный JSON (если есть)
- **Response headers** — заголовки ответа
- **Response body** — JSON ответ
- **Response code** — HTTP статус (200, 404, 500 и т.д.)

### Пример запроса (GET)

**Endpoint:** `GET /api/v1/keys/?tg_id=123456789`

**Response (200 OK):**
```json
[
  {
    "email": "user@vpn.ru",
    "tg_id": 123456789,
    "key": "https://sub.example.com/abc123",
    "expiry_time": 1735689600000,
    "status_text": "Активен",
    "days_left": 45,
    "is_active": true,
    "tariff_id": 1,
    "name_tariff": "Pro",
    "total_gb": 53687091200,
    "used_traffic": 5368709120
  }
]
```

## Добавление X-Bot-Secret Header

**Все endpoints требуют** header `X-Bot-Secret` для аутентификации между сервисами.

### В Swagger UI

К сожалению, Swagger не позволяет добавлять кастомные headers напрямую через UI. Используйте альтернативные методы:

#### Вариант 1: curl (из Swagger)

Swagger показывает команду curl при клике **Execute**. Скопируйте команду и добавьте header:

```bash
curl -X GET "http://localhost:8000/api/v1/keys/?tg_id=123" \
  -H "X-Bot-Secret: your-bot-secret-key" \
  -H "accept: application/json"
```

#### Вариант 2: Postman

1. Скачайте Postman
2. Импортируйте OpenAPI schema из `http://localhost:8000/openapi.json`
3. В каждом запросе добавьте header:
   - **Key:** `X-Bot-Secret`
   - **Value:** `your-bot-secret-key`

#### Вариант 3: httpx / Python

```python
import httpx

async with httpx.AsyncClient() as client:
    response = await client.get(
        "http://localhost:8000/api/v1/keys/?tg_id=123",
        headers={"X-Bot-Secret": "your-bot-secret-key"}
    )
    print(response.json())
```

## Тестирование POST Endpoints

### Пример: Создание нового ключа

**Endpoint:** `POST /api/v1/keys/create`

1. Нажмите на endpoint → **Try it out**
2. В **Request body** введите JSON:
```json
{
  "tg_id": 123456789,
  "tariff_id": 1
}
```

3. Нажмите **Execute**

**Response (200 OK):**
```json
{
  "email": "generated-email@vpn.ru",
  "key": "https://sub.example.com/new-key",
  "expiry_time": 1735689600000,
  "status_text": "Активен"
}
```

**Возможные ошибки:**
- **400 Bad Request** — неверные параметры
- **402 Payment Required** — тариф платный (используйте Payment flow)
- **404 Not Found** — пользователь/тариф не найден
- **500 Internal Server Error** — ошибка на сервере

## Тестирование Payments

### Инициирование платежа

**Endpoint:** `POST /api/v1/payments/create`

**Request body:**
```json
{
  "tg_id": 123456789,
  "tariff_id": 1,
  "operation": "create_key"
}
```

**Response (200 OK):**
```json
{
  "payment_id": "27a9c36b-000f-5000-a000-1f2f9bbb0db1",
  "amount": 299.00,
  "status": "pending",
  "confirmation_url": "https://yookassa.ru/checkout/27a9c36b-000f-5000-a000-1f2f9bbb0db1"
}
```

### Проверка статуса платежа

**Endpoint:** `GET /api/v1/payments/{payment_id}/status`

**Параметры:**
- `payment_id` — ID платежа из ответа выше
- `tg_id` (query) — для проверки владельца

## Тестирование Admin Endpoints

### Проверка здоровья системы

**Endpoint:** `GET /api/v1/admin/health`

**Response (200 OK):**
```json
{
  "status": "ok",
  "database": "connected",
  "cache": "ready",
  "scheduler": "running"
}
```

### Пересборка кеша

**Endpoint:** `POST /api/v1/admin/rebuild-cache`

Требует admin API key (заголовок `X-API-Key`).

**Response (200 OK):**
```json
{
  "status": "cache rebuilt",
  "users": 150,
  "keys": 320,
  "tariffs": 5
}
```

## Понимание Response Schemas

Справа от каждого endpoint отображается раздел **Schemas**, содержащий:

### Models
- **User** — данные пользователя (tg_id, server_id, ref_count)
- **Key** — VPN ключ (email, key, expiry_time, tariff_id)
- **Payment** — платёж (payment_id, amount, status)
- **Tariff** — тариф (id, name, amount, traffic_gb, duration_months)

### Пример схемы Key:
```
email (string) — Email адрес ключа
key (string) — URL подписки на ключ
expiry_time (integer) — Время истечения в Unix timestamp
tariff_id (integer) — ID тарифа
status_text (string) — Статус: "Активен", "Истёк", "Заблокирован"
days_left (integer) — Дней до истечения
is_active (boolean) — Активен ли ключ
```

Нажмите на схему, чтобы развернуть полное описание всех полей.

## Полезные советы

### 1. Сохранение Headers в Browser DevTools

Если вам часто нужно тестировать с `X-Bot-Secret`:

1. Откройте **Browser DevTools** (F12)
2. Перейдите во вкладку **Network**
3. После первого запроса из Swagger скопируйте заголовок
4. Используйте curl для остальных запросов с этим header

### 2. Использование curl напрямую

Скопируйте команду curl из Swagger и модифицируйте:

```bash
# Скопируйте из Swagger и добавьте header:
curl -X GET "http://localhost:8000/api/v1/keys/?tg_id=123" \
  -H "X-Bot-Secret: $(grep BOT_SECRET_KEY ../.env | cut -d= -f2)" \
  -H "accept: application/json"
```

### 3. Тестирование локально с Docker

Если backend запущен в Docker Compose:

```bash
docker-compose up -d
# Swagger доступен на http://localhost:8000/docs
```

### 4. Генерация OpenAPI schema

Swagger schema в JSON формате доступна по:
```
http://localhost:8000/openapi.json
```

Используйте её для импорта в Postman, Insomnia или другие инструменты:

**Postman:**
1. File → Import → Link → `http://localhost:8000/openapi.json`
2. Создаст новую коллекцию со всеми endpoints

**Insomnia:**
1. Create → Request Collection from URL → `http://localhost:8000/openapi.json`

## Тестирование Error Cases

Swagger показывает все возможные HTTP коды для каждого endpoint. Например, `GET /api/v1/keys/{email}`:

- **200 OK** — ключ найден
- **404 Not Found** — ключ не существует
- **401 Unauthorized** — отсутствует X-Bot-Secret
- **500 Internal Server Error** — ошибка на сервере

Чтобы вызвать ошибку, передайте невалидные параметры:

```json
// POST /api/v1/keys/create с платным тарифом
{
  "tg_id": 123,
  "tariff_id": 2  // платный тариф → 402 Payment Required
}
```

## Integration with Tests

Swagger тестирует live endpoint. Для автоматизированного тестирования используйте pytest:

```python
# tests/api/test_keys.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_list_keys(api_client):
    response = await api_client.get("/api/v1/keys/?tg_id=123")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

Запустите тесты:
```bash
cd backend && pytest tests/api/
```

## Отключение Swagger в Production

В production Swagger не должна быть доступна для безопасности. Отключите в `app/main.py`:

```python
# Development
app = FastAPI(title="VPN Platform Backend", lifespan=lifespan)

# Production (disable docs)
if not DEBUG:
    app = FastAPI(
        title="VPN Platform Backend",
        lifespan=lifespan,
        docs_url=None,  # Отключить /docs
        redoc_url=None,  # Отключить /redoc
        openapi_url=None  # Отключить /openapi.json
    )
```

## Troubleshooting

### Swagger не загружается

**Причина:** Backend не запущен или неверный URL

**Решение:**
```bash
# Проверьте, что backend запущен
curl http://localhost:8000/docs

# Или запустите явно
cd backend && uvicorn app.main:app --reload
```

### 401 Unauthorized при тестировании

**Причина:** Отсутствует header `X-Bot-Secret`

**Решение:** Используйте curl с header (см. раздел выше)

### 500 Internal Server Error

**Причина:** Ошибка в коде или отсутствует база данных

**Решение:**
```bash
# Проверьте логи backend
docker-compose logs backend

# Или если локально
# Смотрите вывод в терминале uvicorn
```

### Параметры не принимаются

**Причина:** Неверное имя параметра или тип

**Решение:** Проверьте схему в Swagger (раздел справа). Параметры должны точно соответствовать.

## See Also

- [Backend CLAUDE.md](./CLAUDE.md) — архитектура и endpoints
- [Backend Testing Patterns](./CLAUDE.md#testing-patterns) — примеры тестов
- [FastAPI Docs](https://fastapi.tiangolo.com/deployment/concepts/#about-https) — официальная документация
- [OpenAPI Specification](https://swagger.io/specification/) — стандарт
