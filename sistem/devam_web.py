"""Takipçinin yerel web sayfası — sadece TradingView 'yüksek' alarmlarından gelen sinyaller.

http://127.0.0.1:8765  (yalnız bu bilgisayardan erişilir)
  /, /yuksek  sayfa (sistem/web/yuksek.html)
  /api        filtreler/devam_sinyaller.json içeriği (sayfa "yuksek" alanını kullanır)
"""
from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .liste_yenile import FILTER_DIR

log = logging.getLogger("devam_web")
WEB = Path(__file__).parent / "web"
# 3 Kas 2026: sadece 'yüksek' sekmesi kaldı (sinyaller TradingView alarmlarından, sistem/tv_alarm.py)
SAYFALAR = {"/": WEB / "yuksek.html", "/index.html": WEB / "yuksek.html", "/yuksek": WEB / "yuksek.html",
            "/yukselme": WEB / "yukselme.html"}   # 4 Kas 2026: "yükselme-adayları" tarayıcısı + giriş şartları
# 8 Kas 2026: Gece Yükselişi sayfası kaldırıldı (kullanıcı istedi; yedek/2026-11-08_temizlik_oncesi/)
VERILER = {"/api": FILTER_DIR / "devam_sinyaller.json"}


class _Handler(BaseHTTPRequestHandler):
    def _gonder(self, kod: int, govde: bytes, tur: str) -> None:
        self.send_response(kod)
        self.send_header("Content-Type", tur)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(govde)

    def do_GET(self) -> None:  # noqa: N802 (http.server arayüzü)
        yol = self.path.split("?", 1)[0]
        try:
            if yol in SAYFALAR:
                self._gonder(200, SAYFALAR[yol].read_bytes(), "text/html; charset=utf-8")
            elif yol in VERILER:
                veri = VERILER[yol]
                govde = veri.read_bytes() if veri.exists() else b'{"sinyaller": []}'
                self._gonder(200, govde, "application/json; charset=utf-8")
            else:
                self._gonder(404, b"yok", "text/plain; charset=utf-8")
        except OSError as e:
            log.warning("web: %s", e)
            self._gonder(500, b"okunamadi", "text/plain; charset=utf-8")

    def log_message(self, *args) -> None:  # istek loglarını sustur
        return


def baslat(port: int = 8765) -> None:
    ThreadingHTTPServer(("127.0.0.1", port), _Handler).serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"http://127.0.0.1:8765")
    baslat()
