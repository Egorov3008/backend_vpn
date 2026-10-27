# E2E-тесты всей системы

Сквозные сценарии через настоящие backend, бота, веб-кабинет с админкой и Mini App.
Внешние сервисы заменены заглушками (`fakes.py`): Telegram Bot API, ЮKassa и панель
3x-UI. Прод, реальные платежи и настоящие токены не нужны: секреты стенда
генерируются на каждый запуск и живут только в окружении процессов.

## Что поднимается

| Процесс | Как | Адрес |
|---|---|---|
| Postgres | отдельная база `vpn_e2e`, схема `db/schema_fixed.sql` + тарифы | `E2E_DATABASE_URL` |
| Заглушки | `fakes.py` (Telegram, ЮKassa, 3x-UI, подписка) | `127.0.0.1:19100` |
| backend | `run_backend.py`: обычный `app.main:app`, запросы к `api.telegram.org` и ЮKassa уходят в заглушки | `127.0.0.1:18000` |
| бот | `run_bot.py`: обычный `main.py` из `plaltform-bot`, aiogram смотрит в заглушку | polling |
| веб и админка | nginx с `nginx.conf.template` из `platform-web`, собранный `dist/` | `localhost:18080`, `admin.localhost:18080` |
| Mini App | nginx с `nginx.conf` из `vpn_miniapp`, собранный `dist/` | `localhost:18090` |

nginx-конфиги берутся из репозиториев как есть; меняются только адреса (порт,
`backend:8000`, пути к статике). Плюс `127.0.0.1` объявлен доверенным прокси: так
каждый тест приходит со своим `X-Forwarded-For`, как разные пользователи за внешним
TLS-прокси в проде, и лимиты по IP не мешают друг другу.

## Сценарии

- `test_web_cabinet.py` — гостевой 24-часовой ключ и его срок в панели, вход через бота
  с привязкой ключа, триал, покупка и продление через страницу «ЮKassa» и webhook,
  повторный webhook, повторное «Оплатить» без оплаты, бонус за канал, удаление ключа,
  реферальная ссылка из кабинета в боте.
- `test_bot.py` — онбординг с триалом, `/start landing_<uid>`, вход на сайт: отмена,
  испорченная ссылка, одноразовость, вход существующего пользователя.
- `test_admin.py` — вход в `/admin` по initData админ-бота, выдача и удаление ключа,
  отказ не-админу, чужому боту и протухшему initData, вырезание секретов в nginx,
  рассылка видео с кнопкой приложения (загрузка файла через nginx админки, заблокировавшие бота).
- `test_miniapp.py` — триал и продление триала оплатой, вход вне Telegram.
- `test_security.py` — что видно через nginx кабинета, CSRF-заголовок, секреты для
  маршрутов бота, разделение сессий, лимит гостевых ключей по IP.

## Запуск

Нужны соседние checkout'ы `plaltform-bot`, `platform-web`, `vpn_miniapp` (по умолчанию
рядом с `backend_vpn`), Python 3.11, Node 22, nginx и Postgres 16 с пользователем,
который может создавать базы.

```bash
# окружения backend и бота (как обычно, в их репозиториях)
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
(cd ../plaltform-bot && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt)

# окружение e2e
python3.11 -m venv e2e/.venv && e2e/.venv/bin/pip install -r e2e/requirements.txt
e2e/.venv/bin/playwright install chromium

export E2E_DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/postgres
cd e2e && .venv/bin/python -m pytest          # весь набор, ~1 минута
.venv/bin/python -m pytest -k bot_login      # один сценарий
.venv/bin/python stack.py                     # стенд для ручной проверки, Ctrl+C — стоп
```

База `E2E_DB_NAME` (по умолчанию `vpn_e2e`) пересоздаётся при каждом запуске — не
указывайте имя рабочей базы.

Переменные: `E2E_BOT_REPO`, `E2E_WEB_REPO`, `E2E_MINIAPP_REPO` — пути к репозиториям;
`E2E_BACKEND_PYTHON`, `E2E_BOT_PYTHON` — интерпретаторы (по умолчанию `.venv` репозитория);
`E2E_SKIP_BUILD=1` — не пересобирать фронтенды, если `dist/` уже есть;
`E2E_WITHOUT_BOT=1` — без бота; `E2E_HEADED=1` — браузер с окном.

Логи процессов и скриншоты упавших тестов — в `e2e/.artifacts/`.
