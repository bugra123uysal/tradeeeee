"""TradingView alarmlarından 'yüksek' sinyalleri (3 Kas 2026).

Kullanıcı hisse bulucu izleme listelerine (bugraaa_premarket / bugraaa_trade) pine/yuksek.pine
alarmlarını kurdu. Site artık sinyali kendisi hesaplamıyor: sadece TradingView'de tetiklenen
"yüksek: SİNYAL <SYM> @ <fiyat> · geri çekilme alışı <seviye>" alarmlarını gösterir.
Alarm kaydı (list_fires) açık TradingView Desktop'un oturumuyla CDP üzerinden okunur.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import tv_cdp

NY = ZoneInfo("America/New_York")
FIRES_URL = "https://pricealerts.tradingview.com/list_fires"
FIRES_LIMIT = 1000
_MESAJ = re.compile(r"^yüksek: SİNYAL (\S+) @ ([\d.]+) · geri çekilme alışı ([\d.]+)")


@dataclass(frozen=True)
class AlarmSinyali:
    sym: str          # BORSA:SEMBOL (tarayıcıyla aynı biçim)
    al_dk: int        # sinyal mumunun açılış dakikası (epoch dk)
    fiyat: float
    seviye: float     # Pine'ın yazdığı geri çekilme alış seviyesi
    tetik: float      # alarmın tetiklendiği an (epoch sn)


def _zaman(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _sembol(f: dict) -> str:
    """pro_symbol '={"symbol":"NASDAQ:CHSN",...}' → NASDAQ:CHSN (yoksa symbol alanı)."""
    for alan in ("pro_symbol", "symbol"):
        ham = f.get(alan) or ""
        if ham.startswith("="):
            try:
                ham = json.loads(ham[1:]).get("symbol", "")
            except ValueError:
                ham = ""
        if ham:
            return ham
    return ""


def ayikla(fires: list[dict], gun: date) -> list[AlarmSinyali]:
    """Alarm kaydından bugünkü (NY) 'yüksek: SİNYAL' tetiklenmeleri; iki listeden aynı hisse/mum bir kez."""
    out: dict[tuple[str, int], AlarmSinyali] = {}
    for f in fires:
        m = _MESAJ.match(f.get("message") or "")
        if not m or not f.get("bar_time") or not f.get("fire_time"):
            continue
        bar, tetik = _zaman(f["bar_time"]), _zaman(f["fire_time"])
        sym = _sembol(f)
        if bar.astimezone(NY).date() != gun or not sym:
            continue
        al_dk = int(bar.timestamp() // 60)
        x = AlarmSinyali(sym, al_dk, float(m.group(2)), float(m.group(3)), tetik.timestamp())
        onceki = out.get((sym, al_dk))
        if onceki is None or x.tetik < onceki.tetik:
            out[(sym, al_dk)] = x
    return sorted(out.values(), key=lambda x: x.tetik)


def bugunku_sinyaller(gun: date, port: int = 9222) -> list[AlarmSinyali]:
    r = tv_cdp.request(FIRES_URL, "POST", {"payload": {"limit": FIRES_LIMIT}}, text_plain=True, port=port)
    if not isinstance(r, dict) or r.get("s") != "ok":
        raise tv_cdp.TVError(f"alarm kaydı okunamadı: {str(r)[:200]}")
    return ayikla(r.get("r") or [], gun)
