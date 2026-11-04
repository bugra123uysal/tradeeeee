"""Telegram bildirimi — mesajlar arka planda gönderilir, mum akışını bekletmez."""
from __future__ import annotations

import logging
import queue
import threading

import requests

log = logging.getLogger("telegram")

API = "https://api.telegram.org/bot{token}/sendMessage"
TIMEOUT_SEC = 10


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token.strip(), str(chat_id).strip()
        self.enabled = bool(self.token and self.chat_id)
        self.q: queue.Queue[str] = queue.Queue()
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True).start()
        else:
            log.warning("Telegram ayarlı değil (.env TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) — mesajlar sadece loga yazılır")

    def send(self, text: str) -> None:
        if self.enabled:
            self.q.put(text)
        else:
            log.info("[telegram kapalı] %s", text.replace("\n", " | "))

    def _worker(self) -> None:
        session = requests.Session()
        url = API.format(token=self.token)
        while True:
            text = self.q.get()
            try:
                r = session.post(url, timeout=TIMEOUT_SEC, data={
                    "chat_id": self.chat_id, "text": text,
                    "parse_mode": "HTML", "disable_web_page_preview": "true"})
                if r.status_code != 200:
                    log.error("Telegram %s: %s", r.status_code, r.text[:200])
            except requests.RequestException as e:
                log.error("Telegram gönderilemedi: %s", e)
