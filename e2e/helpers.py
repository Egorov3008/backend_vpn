"""Помощники e2e: подпись initData, «пользователь Telegram» и доступ к заглушкам."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Callable, Optional
from urllib.parse import urlencode

import httpx


def sign_init_data(bot_token: str, tg_id: int, *, auth_date: Optional[int] = None, username: str = "") -> str:
    """``Telegram.WebApp.initData``, подписанный как это делает Telegram."""
    user = {"id": tg_id, "first_name": f"User{tg_id}", "username": username or f"user{tg_id}", "language_code": "ru"}
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": f"e2e{tg_id}",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def telegram_webapp_stub(init_data: str) -> str:
    """Замена https://telegram.org/js/telegram-web-app.js для страниц Mini App.

    ``showConfirm`` сразу отвечает «да»: подтверждения в тестах принимаются."""
    return f"""
window.Telegram = {{ WebApp: {{
  initData: {json.dumps(init_data)},
  initDataUnsafe: {{}},
  colorScheme: "light", themeParams: {{}}, platform: "e2e", version: "8.0",
  ready() {{}}, expand() {{}}, close() {{}},
  onEvent() {{}}, offEvent() {{}}, setHeaderColor() {{}}, setBackgroundColor() {{}},
  openLink(url) {{ window.__openedLinks = (window.__openedLinks || []).concat(url); }},
  openTelegramLink(url) {{ window.__openedLinks = (window.__openedLinks || []).concat(url); }},
  showConfirm(message, cb) {{ cb(true); }},
  showAlert(message, cb) {{ cb && cb(); }},
  HapticFeedback: {{ notificationOccurred() {{}}, impactOccurred() {{}} }},
  BackButton: {{ show() {{}}, hide() {{}}, onClick() {{}}, offClick() {{}} }},
  MainButton: {{ show() {{}}, hide() {{}}, onClick() {{}}, offClick() {{}}, setText() {{}} }},
}} }};
"""


class Fakes:
    """Состояние заглушек (Telegram, ЮKassa, 3x-UI) и действия «пользователя»."""

    def __init__(self, base_url: str) -> None:
        self.base = base_url
        self.http = httpx.Client(base_url=base_url, timeout=30, trust_env=False)

    def state(self) -> dict[str, Any]:
        return self.http.get("/_ctl/state").json()

    # --- Telegram ---

    def sent_to(self, chat_id: int) -> list[dict]:
        return [m for m in self.state()["sent"] if m["chat_id"] == chat_id]

    def block_bot(self, *chat_ids: int) -> None:
        """Эти пользователи заблокировали бота: отправка им — 403."""
        self.http.post("/_ctl/tg/blocked", json={"chat_ids": list(chat_ids)}).raise_for_status()

    def user_says(self, tg_id: int, text: str) -> int:
        """Пользователь пишет боту; возвращает число сообщений ему до этого."""
        before = len(self.sent_to(tg_id))
        self.http.post("/_ctl/tg/message", json={"tg_id": tg_id, "text": text}).raise_for_status()
        return before

    def press(self, tg_id: int, message: dict, button_text: str) -> int:
        """Нажать inline-кнопку сообщения по её тексту (подстрока)."""
        markup = (message["message"].get("reply_markup") or {}).get("inline_keyboard") or []
        for row in markup:
            for button in row:
                if button_text in button.get("text", "") and "callback_data" in button:
                    before = len(self.sent_to(tg_id))
                    self.http.post(
                        "/_ctl/tg/callback",
                        json={"tg_id": tg_id, "message_id": message["message"]["message_id"],
                              "data": button["callback_data"]},
                    ).raise_for_status()
                    return before
        texts = [b.get("text") for row in markup for b in row]
        raise AssertionError(f"Кнопка «{button_text}» не найдена, есть: {texts}")

    def wait_bot_message(
        self, tg_id: int, predicate: Callable[[dict], bool], *, after: int = 0, timeout: float = 20
    ) -> dict:
        deadline = time.time() + timeout
        seen: list[dict] = []
        while time.time() < deadline:
            seen = self.sent_to(tg_id)[after:]
            for m in seen:
                if predicate(m):
                    return m
            time.sleep(0.2)
        texts = [(m["method"], (m["params"].get("text") or m["params"].get("caption") or "")[:120]) for m in seen]
        raise AssertionError(f"Бот не прислал ожидаемое сообщение пользователю {tg_id}. Было: {texts}")

    def wait_text(self, tg_id: int, fragment: str, *, after: int = 0, timeout: float = 20) -> dict:
        return self.wait_bot_message(
            tg_id, lambda m: fragment in (m["params"].get("text") or m["params"].get("caption") or ""),
            after=after, timeout=timeout,
        )

    # --- ЮKassa / 3x-UI ---

    def pay(self, payment_id: str) -> dict:
        return self.http.post(f"/_ctl/yk/{payment_id}/succeed").json()

    def panel_client(self, email: str) -> Optional[dict]:
        return self.state()["clients"].get(email)


def text_of(message: dict) -> str:
    return message["params"].get("text") or message["params"].get("caption") or ""
