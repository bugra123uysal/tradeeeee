"""SEC EDGAR — hissenin son dosyaları (6 Kas 2026). Bedava, gerçek zamanlı; düşük float'ta "balina" izi.

Kategoriler:
  * kayit      : S-1 / S-3 / F-1 / F-3 (+/A, ASR, MEF) — hisse satma İZNİ (raf kaydı); sulandırma riski
  * ihrac      : 424B* — satış FİİLEN yapılıyor (fiyatlı izahname); ralli öldürücü
  * buyuk_ortak: SC 13D / 13G (ve 2025 sonrası "SCHEDULE 13D/G") — biri %5 üstü pay aldı/değiştirdi
  * iceriden   : Form 4 — yönetici/büyük ortak alım-satımı (alım mı satım mı ayrıştırılmıyor)
  * olay       : 8-K — önemli olay (anlaşma, bilanço, ihraç duyurusu…)

SEC otomatik isteklerde iletişim bilgisi ister: .env → SEC_USER_AGENT="ad e-posta". Saniyede en çok 10 istek.
"""
from __future__ import annotations

import json
import os
import threading
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

NY = ZoneInfo("America/New_York")
TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SUB_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SUB_EK_URL = "https://data.sec.gov/submissions/{ad}"
DOSYA_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
SAYFA_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sym}&owner=include&count=40"
MIN_ARALIK = 0.12            # sn — SEC sınırı 10 istek/sn, biraz altında kal

_KAYIT = {"S-1", "S-3", "F-1", "F-3", "S-3ASR", "F-3ASR", "S-1MEF", "F-1MEF", "S-3MEF", "F-3MEF"}
_BUYUK = {"SC 13D", "SC 13G", "SCHEDULE 13D", "SCHEDULE 13G"}
KATEGORILER = ("kayit", "ihrac", "buyuk_ortak", "iceriden", "olay")


def kategori(form: str) -> str | None:
    f = form.upper().strip()
    kok = f[:-2] if f.endswith("/A") else f
    if kok in _KAYIT:
        return "kayit"
    if f.startswith("424B"):
        return "ihrac"
    if kok in _BUYUK:
        return "buyuk_ortak"
    if kok == "4":
        return "iceriden"
    if kok == "8-K":
        return "olay"
    return None


@dataclass(frozen=True)
class Dosya:
    form: str
    kategori: str
    zaman: datetime          # SEC kabul anı (NY)
    url: str


def _zaman(s: str) -> datetime:
    """'2025-11-03T16:05:12.000Z' → NY saati."""
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(NY)


def dosyalar(sub: dict, cik: int) -> list[Dosya]:
    """submissions JSON'unun (veya ek sayfasının) filings dizilerinden ilgili kategorideki dosyalar, eskiden yeniye."""
    r = sub.get("filings", {}).get("recent", sub)
    out = []
    for form, acc, an, doc in zip(r.get("form", []), r.get("accessionNumber", []),
                                  r.get("acceptanceDateTime", []), r.get("primaryDocument", [])):
        k = kategori(form)
        if k is None or not an:
            continue
        url = DOSYA_URL.format(cik=cik, acc=acc.replace("-", ""), doc=doc) if doc else ""
        out.append(Dosya(form, k, _zaman(an), url))
    return sorted(out, key=lambda d: d.zaman)


def ozet(liste: list[Dosya], an: datetime, gun: int = 30) -> dict[str, int]:
    """`an`dan önceki `gun` gün içindeki dosya sayıları (an'dan SONRAKİLER sayılmaz → geçmiş testte ileriye bakış yok)."""
    bas = an - timedelta(days=gun)
    say = dict.fromkeys(KATEGORILER, 0)
    for d in liste:
        if bas <= d.zaman < an:
            say[d.kategori] += 1
    return say


class Edgar:
    """Ticker → CIK eşlemesi + submissions JSON (diskte önbellekli)."""

    def __init__(self, onbellek: Path, ua: str | None = None, ttl: float | None = 600):
        ua = ua or os.getenv("SEC_USER_AGENT", "")
        if "@" not in ua:
            raise ValueError("SEC_USER_AGENT .env'de 'ad e-posta' biçiminde olmalı (SEC şartı)")
        self.dizin, self.ttl = onbellek, ttl          # ttl=None → önbellek hiç eskimez (geçmiş testi)
        self.dizin.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": ua, "Accept-Encoding": "gzip, deflate"})
        self._kilit, self._son = threading.Lock(), 0.0
        self._cik: dict[str, int] | None = None

    def _get(self, url: str) -> dict:
        with self._kilit:
            bekle = MIN_ARALIK - (_time.monotonic() - self._son)
            if bekle > 0:
                _time.sleep(bekle)
            self._son = _time.monotonic()
        r = self.s.get(url, timeout=20)
        r.raise_for_status()
        return r.json()

    def _onbellekli(self, ad: str, url: str, ttl: float | None) -> dict:
        p = self.dizin / ad
        if p.exists() and (ttl is None or _time.time() - p.stat().st_mtime < ttl):
            return json.loads(p.read_text(encoding="utf-8"))
        veri = self._get(url)
        p.write_text(json.dumps(veri), encoding="utf-8")
        return veri

    def cik(self, sym: str) -> int | None:
        if self._cik is None:
            t = self._onbellekli("company_tickers.json", TICKER_URL, 86400 if self.ttl is not None else None)
            self._cik = {v["ticker"].upper(): int(v["cik_str"]) for v in t.values()}
        return self._cik.get(sym.split(":")[-1].upper().replace(".", "-"))

    def hisse(self, sym: str, eski_sayfalar: bool = False) -> list[Dosya]:
        """Hissenin ilgili dosyaları. CIK bulunamazsa (yabancı/OTC/ad değişikliği) boş liste."""
        c = self.cik(sym)
        if c is None:
            return []
        sub = self._onbellekli(f"CIK{c:010d}.json", SUB_URL.format(cik=c), self.ttl)
        out = dosyalar(sub, c)
        if eski_sayfalar:
            for f in sub.get("filings", {}).get("files", []):
                out += dosyalar(self._onbellekli(f["name"], SUB_EK_URL.format(ad=f["name"]), self.ttl), c)
        return sorted(set(out), key=lambda d: d.zaman)
