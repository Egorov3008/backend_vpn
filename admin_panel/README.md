# Admin panel

Vanilla-JS SPA, no build step, consuming `/api/v1/admin/*`. Served by FastAPI via
`StaticFiles` mounted at `/admin-panel` (see `app/main.py`). Routing is hash-based
(`#/users`, `#/keys`, …) since `StaticFiles` doesn't rewrite arbitrary sub-paths to
`index.html`.

## Running locally

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000/admin-panel/#/login`, enter the `ADMIN_API_KEY` from
`.env` and your Telegram ID.

## Verification

`pytest` covers only that the static mount doesn't break existing backend routes —
it cannot verify client-side rendering, routing, or fetch wiring. Any change to this
panel must additionally be driven through a real browser (e.g. Playwright) covering
at minimum:

1. Bad API key → inline error, no redirect.
2. Valid API key → redirect to `#/dashboard`, stats render.
3. `#/users` — paginated table renders, prev/next work, `X-Total-Count` respected.
4. One full destructive round-trip — toggling maintenance mode on the dashboard is
   the safe choice (fully reversible, no effect on real user/key data).
5. Corrupting the stored API key → next request triggers auto-logout + redirect.
6. No uncaught JS errors in the browser console at any step.

## Scope note

Mass mailing/broadcast (present in the Telegram bot's admin panel) has no backend
endpoint in `api/v1/admin.py` and is intentionally out of scope here — the nav shows
a disabled entry pointing back to the bot instead of silently omitting it.
