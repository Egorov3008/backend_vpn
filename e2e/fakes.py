"""Заглушки внешних сервисов для e2e: Telegram Bot API, ЮKassa, панель 3x-UI.

Один процесс, один порт, разные префиксы:

- ``/bot<token>/<method>`` — Telegram Bot API (aiogram бота и httpx backend'а).
- ``/v3/payments`` — ЮKassa (SDK ``yookassa`` с подменённым ``api_url``).
- ``/yk/checkout/<id>`` — страница «оплаты»: кнопка помечает платёж
  оплаченным, шлёт webhook в backend и уводит на ``return_url``.
- ``/panel/...`` — standalone API 3x-UI v3.2.0; ``/sub/<email>`` — подписка.
- ``/_ctl/...`` — управление из тестов: сброс, апдейты в бота, отправленные
  ботом сообщения, оплата счёта.

Состояние в памяти процесса, наружу ничего не ходит.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
import uuid
from typing import Any, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

app = FastAPI(title="e2e fakes")


class State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        # Telegram
        self.bots: dict[str, dict] = {}  # token -> getMe result
        self.updates: list[dict] = []
        self.update_seq = 1
        self.update_event = asyncio.Event()
        self.sent: list[dict] = []  # всё, что боты отправили/отредактировали
        self.msg_seq = 1000
        self.callback_answers: list[dict] = []
        self.chat_members: dict[tuple[str, int], str] = {}
        # ЮKassa
        self.payments: dict[str, dict] = {}
        # Адрес webhook, как в настройках магазина ЮKassa (в запросе на создание
        # платежа его нет: SDK такое поле не передаёт)
        self.webhook_url: Optional[str] = None
        self.yk_requests: list[dict] = []
        # 3x-UI
        self.inbounds: list[int] = []
        self.clients: dict[str, dict] = {}  # email -> {"client": {...}, "inboundIds": [...]}
        self.panel_calls: list[dict] = []


S = State()


# --- управление ---------------------------------------------------------------


@app.post("/_ctl/reset")
async def ctl_reset(request: Request) -> dict:
    body = await request.json()
    keep_updates_event = S.update_event
    S.reset()
    S.update_event = keep_updates_event
    for token, me in (body.get("bots") or {}).items():
        S.bots[token] = me
    S.inbounds = list(body.get("inbounds") or [])
    S.webhook_url = body.get("webhook_url")
    return {"ok": True}


@app.get("/_ctl/state")
async def ctl_state() -> dict:
    return {
        "sent": S.sent,
        "callback_answers": S.callback_answers,
        "payments": S.payments,
        "yk_requests": S.yk_requests,
        "clients": S.clients,
        "panel_calls": S.panel_calls,
        "pending_updates": len(S.updates),
    }


def _user(tg_id: int, username: Optional[str] = None) -> dict:
    return {
        "id": tg_id,
        "is_bot": False,
        "first_name": f"User{tg_id}",
        "username": username or f"user{tg_id}",
        "language_code": "ru",
    }


def _push_update(payload: dict) -> int:
    update_id = S.update_seq
    S.update_seq += 1
    S.updates.append({"update_id": update_id, **payload})
    S.update_event.set()
    return update_id


@app.post("/_ctl/tg/message")
async def ctl_tg_message(request: Request) -> dict:
    """Пользователь пишет боту: {tg_id, text}."""
    body = await request.json()
    tg_id = int(body["tg_id"])
    S.msg_seq += 1
    text = body["text"]
    message: dict[str, Any] = {
        "message_id": S.msg_seq,
        "date": int(time.time()),
        "chat": {"id": tg_id, "type": "private", "first_name": f"User{tg_id}"},
        "from": _user(tg_id, body.get("username")),
        "text": text,
    }
    if text.startswith("/"):
        message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {"update_id": _push_update({"message": message})}


@app.post("/_ctl/tg/callback")
async def ctl_tg_callback(request: Request) -> dict:
    """Пользователь нажал inline-кнопку: {tg_id, message_id, data}."""
    body = await request.json()
    tg_id = int(body["tg_id"])
    msg = _find_sent(tg_id, int(body["message_id"]))
    return {
        "update_id": _push_update(
            {
                "callback_query": {
                    "id": uuid.uuid4().hex,
                    "from": _user(tg_id),
                    "chat_instance": str(tg_id),
                    "data": body["data"],
                    "message": msg["message"] if msg else {
                        "message_id": int(body["message_id"]),
                        "date": int(time.time()),
                        "chat": {"id": tg_id, "type": "private"},
                        "text": "",
                    },
                }
            }
        )
    }


@app.post("/_ctl/tg/chat-member")
async def ctl_tg_chat_member(request: Request) -> dict:
    body = await request.json()
    S.chat_members[(str(body["chat_id"]), int(body["user_id"]))] = body["status"]
    return {"ok": True}


def _find_sent(chat_id: int, message_id: int) -> Optional[dict]:
    for item in reversed(S.sent):
        if item["chat_id"] == chat_id and item["message"]["message_id"] == message_id:
            return item
    return None


@app.post("/_ctl/yk/{payment_id}/succeed")
async def ctl_yk_succeed(payment_id: str) -> dict:
    """Счёт оплачен: статус succeeded + webhook в backend, как у ЮKassa."""
    status, body = await _succeed_and_notify(payment_id)
    return {"webhook_status": status, "webhook_body": body}


async def _succeed_and_notify(payment_id: str) -> tuple[int, Any]:
    payment = S.payments.get(payment_id)
    if not payment:
        raise HTTPException(404, "payment not found")
    payment["status"] = "succeeded"
    payment["paid"] = True
    payment["captured_at"] = _iso()
    url = S.webhook_url
    async with httpx.AsyncClient(timeout=60, trust_env=False) as client:
        resp = await client.post(
            url,
            json={"type": "notification", "event": "payment.succeeded", "object": _public_payment(payment)},
        )
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, resp.text


# --- Telegram Bot API ---------------------------------------------------------


async def _tg_params(request: Request) -> dict:
    ctype = request.headers.get("content-type", "")
    params: dict[str, Any] = dict(request.query_params)
    if "json" in ctype:
        params.update(await request.json())
    elif "form" in ctype:
        form = await request.form()
        for k, v in form.items():
            params[k] = v if isinstance(v, str) else f"<file {getattr(v, 'filename', '')}>"
    for k in ("reply_markup", "allowed_updates", "commands", "scope", "link_preview_options"):
        if isinstance(params.get(k), str):
            try:
                params[k] = json.loads(params[k])
            except ValueError:
                pass
    return params


def _ok(result: Any) -> JSONResponse:
    return JSONResponse({"ok": True, "result": result})


def _record(token: str, method: str, chat_id: int, params: dict, message: dict) -> None:
    S.sent.append(
        {"bot": S.bots.get(token, {}).get("username"), "method": method, "chat_id": chat_id,
         "params": params, "message": message}
    )


@app.api_route("/bot{token}/{method}", methods=["GET", "POST"])
async def telegram(token: str, method: str, request: Request):
    me = S.bots.get(token)
    if me is None:
        return JSONResponse({"ok": False, "error_code": 401, "description": "Unauthorized"}, status_code=401)
    params = await _tg_params(request)
    m = method.lower()

    if m == "getme":
        return _ok(me)
    if m == "getupdates":
        offset = int(params.get("offset") or 0)
        timeout = float(params.get("timeout") or 0)
        S.updates = [u for u in S.updates if u["update_id"] >= offset]
        if not S.updates and timeout:
            S.update_event.clear()
            try:
                await asyncio.wait_for(S.update_event.wait(), timeout=min(timeout, 2))
            except asyncio.TimeoutError:
                pass
        return _ok(S.updates[:100])
    if m in ("deletewebhook", "setmycommands", "deletemycommands", "setchatmenubutton", "sendchataction",
             "deletemessage", "setmydescription", "setmyshortdescription"):
        return _ok(True)
    if m == "answercallbackquery":
        S.callback_answers.append(params)
        return _ok(True)
    if m == "getchatmember":
        user_id = int(params.get("user_id"))
        status = S.chat_members.get((str(params.get("chat_id")), user_id)) or S.chat_members.get(("*", user_id), "left")
        return _ok({"status": status, "user": _user(int(params.get("user_id")))})
    if m == "getchat":
        return _ok({"id": params.get("chat_id"), "type": "private"})
    if m.startswith("send") or m.startswith("edit") or m == "copymessage":
        chat_id = int(params.get("chat_id") or 0)
        if m.startswith("edit") and params.get("message_id"):
            message_id = int(params["message_id"])
            prev = _find_sent(chat_id, message_id)
            message = dict(prev["message"]) if prev else {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}}
        else:
            S.msg_seq += 1
            message = {"message_id": S.msg_seq, "chat": {"id": chat_id, "type": "private"}}
        message.update({"date": int(time.time()), "from": me})
        if "text" in params:
            message["text"] = params["text"]
        if "caption" in params:
            message["caption"] = params["caption"]
        if "reply_markup" in params:
            message["reply_markup"] = params["reply_markup"]
        if m == "sendphoto":
            message["photo"] = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
        _record(token, method, chat_id, params, message)
        return _ok(message)
    # Неизвестный метод — пусть тест увидит, что бот звал что-то новое
    _record(token, method, int(params.get("chat_id") or 0), params, {"message_id": 0})
    return _ok(True)


# --- ЮKassa -------------------------------------------------------------------


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _public_payment(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}


@app.post("/v3/payments")
async def yk_create(request: Request):
    if not request.headers.get("authorization", "").startswith("Basic "):
        return JSONResponse({"type": "error", "code": "invalid_credentials"}, status_code=401)
    body = await request.json()
    S.yk_requests.append(body)
    payment_id = f"e2e-{uuid.uuid4()}"
    base = str(request.base_url).rstrip("/")
    payment = {
        "id": payment_id,
        "status": "pending",
        "paid": False,
        "amount": body["amount"],
        "confirmation": {
            "type": "redirect",
            "return_url": body["confirmation"]["return_url"],
            "confirmation_url": f"{base}/yk/checkout/{payment_id}",
        },
        "created_at": _iso(),
        "description": body.get("description", ""),
        "metadata": body.get("metadata", {}),
        "recipient": {"account_id": "e2e", "gateway_id": "e2e"},
        "refundable": False,
        "test": True,
    }
    S.payments[payment_id] = payment
    return _public_payment(payment)


@app.get("/v3/payments/{payment_id}")
async def yk_get(payment_id: str):
    payment = S.payments.get(payment_id)
    if not payment:
        return JSONResponse({"type": "error", "code": "not_found"}, status_code=404)
    return _public_payment(payment)


@app.get("/yk/checkout/{payment_id}", response_class=HTMLResponse)
async def yk_checkout(payment_id: str):
    p = S.payments.get(payment_id)
    if not p:
        raise HTTPException(404)
    return f"""<!doctype html><meta charset=utf-8><title>Тестовая оплата</title>
<h1>Тестовая оплата {p['amount']['value']} {p['amount']['currency']}</h1>
<form method=post action="/yk/checkout/{payment_id}/pay"><button id=pay>Оплатить</button></form>"""


@app.post("/yk/checkout/{payment_id}/pay")
async def yk_checkout_pay(payment_id: str):
    await _succeed_and_notify(payment_id)
    return RedirectResponse(S.payments[payment_id]["confirmation"]["return_url"], status_code=303)


# --- 3x-UI --------------------------------------------------------------------


def _panel_ok(obj: Any = None) -> dict:
    return {"success": True, "msg": "", "obj": obj}


@app.get("/panel/csrf-token")
async def panel_csrf():
    resp = JSONResponse({"success": True, "obj": "e2e-csrf"})
    resp.set_cookie("3x-ui", "pre-login")
    return resp


@app.post("/panel/login")
async def panel_login():
    resp = JSONResponse(_panel_ok())
    resp.set_cookie("3x-ui", "e2e-session")
    return resp


@app.api_route("/panel/api/{path:path}", methods=["GET", "POST"])
async def panel_api(path: str, request: Request):
    body: Any = None
    if request.method == "POST" and request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
    S.panel_calls.append({"method": request.method, "path": path, "body": body})
    parts = path.split("/")

    if path == "inbounds/list":
        return _panel_ok([{"id": i, "remark": f"inbound-{i}", "enable": True, "protocol": "vless"} for i in S.inbounds])
    if path == "clients/add":
        client = body["client"]
        email = client["email"]
        if email in S.clients:
            return {"success": False, "msg": "Duplicate email", "obj": None}
        unknown = [i for i in body.get("inboundIds", []) if i not in S.inbounds]
        if unknown:
            return {"success": False, "msg": f"inbound not found: {unknown}", "obj": None}
        S.clients[email] = {"client": dict(client), "inboundIds": list(body.get("inboundIds", []))}
        return _panel_ok(S.clients[email])
    if path in ("clients/list", "clients/list/paged"):
        items = [dict(c["client"], inboundIds=c["inboundIds"]) for c in S.clients.values()]
        if path.endswith("paged"):
            return _panel_ok({"items": items, "total": len(items)})
        return _panel_ok(items)
    if path == "clients/onlines":
        return _panel_ok([])
    if path in ("clients/bulkAttach", "clients/bulkDetach"):
        for email in body.get("emails", []):
            _attach_detach(email, body.get("inboundIds", []), attach=path.endswith("Attach"))
        return _panel_ok()

    if len(parts) == 3 and parts[0] == "clients":
        action, email = parts[1], parts[2]
        if action == "get":
            if email not in S.clients:
                return JSONResponse({"success": False, "msg": "client not found"}, status_code=404)
            return _panel_ok(S.clients[email])
        if action == "update":
            if email not in S.clients:
                return {"success": False, "msg": "client not found"}
            fields = {k: v for k, v in body.items() if k != "inboundIds"}
            S.clients[email]["client"].update(fields)
            return _panel_ok()
        if action == "del":
            S.clients.pop(email, None)
            return _panel_ok()
        if action == "resetTraffic":
            return _panel_ok()
        # clients/<email>/attach | detach | externalLinks
        email, action = parts[1], parts[2]
        if action in ("attach", "detach"):
            _attach_detach(email, body.get("inboundIds", []), attach=action == "attach")
            return _panel_ok()
        if action == "externalLinks":
            if email in S.clients:
                S.clients[email]["externalLinks"] = body.get("externalLinks")
            return _panel_ok()
    return JSONResponse({"success": False, "msg": f"e2e fake: unknown panel path {path}"}, status_code=404)


def _attach_detach(email: str, inbound_ids: list[int], attach: bool) -> None:
    c = S.clients.get(email)
    if not c:
        return
    ids = set(c["inboundIds"])
    ids = ids | set(inbound_ids) if attach else ids - set(inbound_ids)
    c["inboundIds"] = sorted(ids)


@app.get("/sub/{email}")
async def subscription(email: str):
    c = S.clients.get(email)
    if not c:
        raise HTTPException(404)
    vless = f"vless://{c['client']['id']}@127.0.0.1:443?type=tcp&security=reality#{email}"
    return HTMLResponse(base64.b64encode(vless.encode()).decode(), media_type="text/plain")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
