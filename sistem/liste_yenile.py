"""bugraaa_premarket ve bugraaa_trade izleme listelerini sürekli yeniler.

Kurallar filtreler/AJAN_GOREVI.md'de:
  * Premarket (04:00–09:30 NY): filtreler/premarket.json → bugraaa_premarket
  * Piyasa saati (09:30–kapanış NY): filtreler/piyasa.json → bugraaa_trade
    (bugraaa_premarket açılışta silinmez, son premarket listesi gün boyu kalır)
  * Hafta sonu / ABD tatili: çalışmaz (Alpaca takvimi). Yarım günlerde kapanış takvimden.
  * Listeye yeni giren hisseler loga ve (ayarlıysa) Telegram'a yazılır.
  * Hisseler iki bölüme ayrılır (backtest/ sonucu, 1 Eyl–30 Eki 2026, 1.930 premarket adayı):
      premarket: "1 Daha sakin" = yükseliş %5–20, 09:25'te premarket tepesine %5'ten yakın,
                 premarket hacmi < 10 günlük ort. hacim. Diğerleri "2 Sönme riski":
                 %20+ yükselenler açılıştan 11:00'e ort. −%3,6 / −%5,6, tepeden düşenler −%1,6.
      piyasa:    "1 Daha sakin" = premarket'te koşmamış (≤ %5) ve float > 20M.
                 float < 20M olanlar ilk girdikleri andan kapanışa ort. −%2,5.

TradingView Desktop açık ve CDP portu (9222) etkin olmalı — listeler kullanıcının
oturumuyla, açık TradingView sayfasının içinden yazılır.

Kullanım:
    python -m sistem.liste_yenile            # NY kapanışına kadar döngü (premarket ve piyasa 30 sn)
    python -m sistem.liste_yenile --once     # tek tur
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from . import tv_cdp

log = logging.getLogger("liste")

# engine.py'den almıyoruz: o pandas yüklüyor, Windows Uygulama Denetimi pandas DLL'ini engelleyebiliyor
NY = ZoneInfo("America/New_York")

ROOT = Path(__file__).resolve().parent.parent
FILTER_DIR = ROOT / "filtreler"
PRE_START, REG_START, REG_END = time(4, 0), time(9, 30), time(16, 0)
MAX_SYMBOLS = 100
CALM_MAX_PM_GAIN = 20.0       # %
CALM_MAX_FADE = -0.05         # premarket tepesinden en fazla %5 aşağıda
CALM_MAX_PM_REL = 1.0         # premarket hacmi / 10 günlük ort. hacim
CALM_MIN_FLOAT = 20e6
ALPACA_CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"
HEADER_CALM = "###1 Daha sakin"
HEADER_RISKY = "###2 Sönme riski yüksek"


@dataclass(frozen=True)
class Session:
    key: str            # premarket / piyasa
    filter_file: str
    list_name: str
    change_col: str


PREMARKET = Session("premarket", "premarket.json", "bugraaa_premarket", "premarket_change")
PIYASA = Session("piyasa", "piyasa.json", "bugraaa_trade", "change")


def session_for(now: datetime, close: time | None) -> Session | None:
    """now: NY saati. close: bugünün kapanışı (None → bugün işlem günü değil)."""
    if close is None:
        return None
    t = now.time()
    if PRE_START <= t < REG_START:
        return PREMARKET
    if REG_START <= t < close:
        return PIYASA
    return None


def is_calm(row: dict, s: Session) -> bool:
    if s is PREMARKET:
        gain, close, high = row.get("premarket_change"), row.get("premarket_close"), row.get("premarket_high")
        vol, avg = row.get("premarket_volume") or 0, row.get("average_volume_10d_calc")
        if gain is None or not close or not high or not avg:
            return False
        return gain <= CALM_MAX_PM_GAIN and close / high - 1 > CALM_MAX_FADE and vol / avg < CALM_MAX_PM_REL
    pm, fl = row.get("premarket_change"), row.get("float_shares_outstanding")
    return (pm is None or pm <= 5) and fl is not None and fl > CALM_MIN_FLOAT


def grouped(rows: list[dict], s: Session) -> list[str]:
    """TradingView listesine yazılacak sıra: bölüm başlıkları + semboller (tarama sırası korunur)."""
    calm = [r["s"] for r in rows if is_calm(r, s)]
    risky = [r["s"] for r in rows if not is_calm(r, s)]
    return ([HEADER_CALM, *calm] if calm else []) + ([HEADER_RISKY, *risky] if risky else [])


def new_entries(old: list[str], new: list[str]) -> list[str]:
    seen = set(old)
    return [s for s in new if s not in seen]


def todays_close(day: date) -> time | None:
    """Alpaca takviminden bugünün kapanış saati; anahtar yoksa hafta içi 16:00 varsayılır."""
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not (key and secret):
        return REG_END if day.weekday() < 5 else None
    # alpaca-py yerine düz REST: alpaca-py pandas yüklüyor (Uygulama Denetimi engeli)
    import requests
    try:
        r = requests.get(ALPACA_CALENDAR_URL, params={"start": day.isoformat(), "end": day.isoformat()},
                         headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=15)
        r.raise_for_status()
        days = [d for d in r.json() if d.get("date") == day.isoformat()]
    except (requests.RequestException, ValueError) as e:
        log.warning("Alpaca takvimi alınamadı (%s) — hafta içi 16:00 varsayılıyor", e)
        return REG_END if day.weekday() < 5 else None
    return time.fromisoformat(days[0]["close"]) if days else None


def load_filter(s: Session) -> dict:
    return json.loads((FILTER_DIR / s.filter_file).read_text(encoding="utf-8"))


def state_path(s: Session) -> Path:
    return FILTER_DIR / f"son_liste_{s.key}.json"


def load_state(s: Session) -> list[str]:
    p = state_path(s)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def fmt_entry(row: dict, s: Session) -> str:
    chg = row.get(s.change_col) or 0
    extra = ""
    if s is PIYASA and row.get("relative_volume_10d_calc"):
        extra = f" · hacim {row['relative_volume_10d_calc']:.1f}x"
    elif s is PREMARKET and row.get("premarket_volume"):
        extra = f" · pm hacim {row['premarket_volume'] / 1000:,.0f}K"
    return f"{row['s'].split(':')[1]} {chg:+.1f}%{extra}"


def refresh(s: Session, notify, port: int) -> list[str]:
    rows = tv_cdp.scan(load_filter(s), port)[:MAX_SYMBOLS]
    symbols = [r["s"] for r in rows]
    old = load_state(s)
    if symbols == old:
        log.info("%s: değişiklik yok (%d hisse)", s.list_name, len(symbols))
        return []
    content = grouped(rows, s)
    lists = {l["name"]: l for l in tv_cdp.custom_lists(port)}
    if s.list_name in lists:
        tv_cdp.replace_list(lists[s.list_name]["id"], content, port)
    else:
        tv_cdp.create_list(s.list_name, content, port)
    state_path(s).write_text(json.dumps(symbols), encoding="utf-8")
    added = new_entries(old, symbols)
    log.info("%s: %d hisse, %d yeni", s.list_name, len(symbols), len(added))
    if added:
        by_sym = {r["s"]: r for r in rows}
        lines = [fmt_entry(by_sym[x], s) + ("" if is_calm(by_sym[x], s) else " ⚠") for x in added]
        _append_log(s, lines)
        notify(f"🆕 {s.list_name}\n" + "\n".join(lines))
    return added


def _append_log(s: Session, lines: list[str]) -> None:
    path = FILTER_DIR / "liste_log.csv"
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new_file:
            w.writerow(["zaman (NY)", "liste", "yeni hisse"])
        now = datetime.now(NY).strftime("%Y-%m-%d %H:%M")
        for line in lines:
            w.writerow([now, s.list_name, line])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=30, help="premarket yenileme aralığı (saniye)")
    ap.add_argument("--interval-market", type=int, default=30, help="piyasa saati yenileme aralığı (saniye)")
    ap.add_argument("--port", type=int, default=9222)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(FILTER_DIR / "liste_yenile.log", encoding="utf-8")])
    load_dotenv(ROOT / ".env")
    from .telegram import Telegram
    # (5 Kas 2026) Telegram'a sadece GİR ve Yükselme Adayları gitsin (devam_takip) → liste değişiklikleri yalnız loga
    tg = Telegram("", "")

    close, close_day = None, None
    while True:
        now = datetime.now(NY)
        if close_day != now.date():
            close, close_day = todays_close(now.date()), now.date()
            log.info("bugün %s: %s", now.date(), f"kapanış {close}" if close else "işlem günü değil")
        s = session_for(now, close)
        if s is None and (close is None or now.time() >= close):
            log.info("seans bitti / işlem günü değil — çıkılıyor")
            return
        if s is not None:
            try:
                refresh(s, tg.send, a.port)
            except tv_cdp.TVError as e:
                log.warning("%s", e)
        if a.once:
            return
        _time.sleep(a.interval_market if s is PIYASA else a.interval)


if __name__ == "__main__":
    main()
