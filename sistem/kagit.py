"""Alpaca KAĞIT (paper) hesapta 'yüksek' sinyallerini otomatik işler (4 Kas 2026) — SAHTE PARA.

Amaç: backtest'in iyimser varsayımlarını (limit dolumu, kayma) gerçek emir akışıyla ölçmek.
Kural sitedekiyle aynı:
  TV 'yüksek' alarmı (gün ≤ FILTRE_GUN_MAX, sinyal ≥ FILTRE_SAAT, en çok PENCERE_DK eski) → sinyal×(1−%5) limit alış
  (30 dk geçerli). Sitedeki 3. şart (sakin geri çekilme / kırmızı oranı) dolumda belli olur — limit emre uygulanmaz.
  → dolunca YARI-YARI çıkış: yarısına dolum×(1+HEDEF) limit satış, kalan yarısına dolum×(1+HEDEF2) limit satış
  → 1 dk kapanış < dolum×(1−STOP) ise açık hedefler iptal + kalan satılır → 15:55 NY'de açık kalan kapatılır.
Güvenlik: sadece paper-api adresi; hesap numarası 'PA' ile başlamıyorsa çalışmaz. Sabit dolar tutarı,
en çok MAX_ACIK pozisyon, günlük zarar sınırı. KAGIT_ACIK=0 → tamamen kapalı.
Not: Alpaca kağıt dolumları da simülasyondur (kuyruk/kısmi dolum gerçekçi değil) — gerçek hesaptan iyimser olabilir.
"""
from __future__ import annotations

import json
import logging
import math
import os
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path

import requests

from . import geri_al as G
from . import yuksek as Y

log = logging.getLogger("kagit")
PAPER = "https://paper-api.alpaca.markets/v2"
QUOTE = "https://data.alpaca.markets/v2/stocks/quotes/latest"
NY_KAPAT = time(15, 55)
REG_OPEN, REG_CLOSE = time(9, 30), time(16, 0)


def _env_f(ad: str, varsayilan: float) -> float:
    try:
        return float(os.getenv(ad, varsayilan))
    except ValueError:
        return varsayilan


@dataclass
class Islem:
    sym: str
    al_dk: int
    seviye: float
    qty: int
    durum: str = "emir"               # emir / doldu / kapandi / iptal / hata
    emir_id: str | None = None
    emir_zaman: float | None = None
    dolum_fiyat: float | None = None
    dolum_zaman: float | None = None
    hedef_id: str | None = None       # 1. bacak (+HEDEF, yarısı)
    hedef2_id: str | None = None      # 2. bacak (+HEDEF2, kalan yarı)
    qty1: int = 0
    yari_zaman: float | None = None   # 1. hedef doldu (yarısı satıldı)
    satislar: list = field(default_factory=list)   # [{qty, fiyat, neden}]
    cikis_id: str | None = None
    cikis_zaman: float | None = None
    cikis_neden: str | None = None    # hedef (ikisi de) / stop / gün sonu
    bid: float | None = None          # sinyal anı IEX kotasyonu (ücretsiz veri, sığ hissede güvenilmez)
    ask: float | None = None
    hata: str | None = None

    @property
    def satilan(self) -> int:
        return sum(int(x["qty"]) for x in self.satislar)

    @property
    def cikis_fiyat(self) -> float | None:
        q = self.satilan
        return sum(x["qty"] * x["fiyat"] for x in self.satislar) / q if q else None

    @property
    def kar_usd(self) -> float | None:
        if not self.dolum_fiyat or not self.satislar:
            return None
        return sum((x["fiyat"] - self.dolum_fiyat) * x["qty"] for x in self.satislar)

    @property
    def kar_yuzde(self) -> float | None:
        k = self.kar_usd
        return k / (self.dolum_fiyat * self.satilan) * 100 if k is not None and self.satilan else None


def _fiyat(p: float, yukari: bool = False) -> float:
    """Alpaca kuralı: ≥ 1 $ → 2 ondalık, < 1 $ → 4 ondalık. Alış aşağı, hedef satış yukarı yuvarlanır."""
    kat = 100 if p >= 1 else 10_000
    n = round(p * kat, 6)                     # 1.9 × 1.05 = 1.99499… gibi kayan nokta artıklarını temizle
    return (math.ceil(n) if yukari else math.floor(n)) / kat


class KagitHesap:
    def __init__(self, dosya: Path) -> None:
        self.dosya = dosya
        self.acik = os.getenv("KAGIT_ACIK", "1") == "1"
        self.usd = _env_f("KAGIT_USD", 1000.0)
        self.max_acik = int(_env_f("KAGIT_MAX_ACIK", 5))
        self.gunluk_zarar = _env_f("KAGIT_GUNLUK_ZARAR_USD", 300.0)
        self.hedef = _env_f("KAGIT_HEDEF", Y.HEDEF1)
        self.hedef2 = _env_f("KAGIT_HEDEF2", Y.HEDEF2)
        self.stop = _env_f("KAGIT_STOP", G.STOP)
        self.islemler: list[Islem] = []
        self.gun = ""
        self.durdu: str | None = None
        self.h = {"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY", ""),
                  "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY", "")}
        if self.acik:
            self._hesap_kontrol()

    # ---- Alpaca ----
    def _istek(self, yontem: str, yol: str, **kw) -> dict | list | None:
        r = requests.request(yontem, PAPER + yol, headers=self.h, timeout=20, **kw)
        if r.status_code == 404 and yontem == "DELETE":
            return None
        if r.status_code >= 400:
            raise RuntimeError(f"{yontem} {yol} → {r.status_code}: {r.text[:200]}")
        return r.json() if r.text else None

    def _hesap_kontrol(self) -> None:
        try:
            a = self._istek("GET", "/account")
        except (requests.RequestException, RuntimeError) as e:
            self.acik, self.durdu = False, f"hesap okunamadı: {e}"
            log.warning("kağıt hesap kapalı: %s", self.durdu)
            return
        if not str(a.get("account_number", "")).startswith("PA"):
            self.acik, self.durdu = False, "hesap kağıt (PA) değil — güvenlik için kapatıldı"
            log.error(self.durdu)

    def _kota(self, sym: str) -> tuple[float | None, float | None]:
        try:
            r = requests.get(QUOTE, params={"symbols": sym, "feed": "iex"}, headers=self.h, timeout=10).json()
            q = (r.get("quotes") or {}).get(sym) or {}
            return q.get("bp") or None, q.get("ap") or None
        except (requests.RequestException, ValueError):
            return None, None

    def _emir(self, sym: str, qty: int, taraf: str, fiyat: float | None, uzatilmis: bool, yukari: bool = False) -> str:
        govde = {"symbol": sym, "qty": str(qty), "side": taraf, "time_in_force": "day"}
        if fiyat is None:
            govde["type"] = "market"
        else:
            govde.update(type="limit", limit_price=str(_fiyat(fiyat, yukari)), extended_hours=uzatilmis)
        return self._istek("POST", "/orders", json=govde)["id"]

    # ---- kayıt ----
    def yukle(self, gun: str) -> None:
        self.gun = gun
        try:
            d = json.loads(self.dosya.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if d.get("gun") == gun:
            self.islemler = [Islem(**{k: v for k, v in x.items()
                                      if k not in ("kar_yuzde", "kar_usd", "cikis_fiyat", "satilan")})
                             for x in d.get("islemler", [])]

    def kaydet(self) -> None:
        tmp = self.dosya.with_suffix(".tmp")
        tmp.write_text(json.dumps({"gun": self.gun, "islemler": self.ozet()["islemler"]}, ensure_ascii=False,
                                  indent=1), encoding="utf-8")
        for i in range(5):                    # Windows: dosya başka süreçte açıksa 'Erişim engellendi' → bekle, tekrar
            try:
                tmp.replace(self.dosya)
                return
            except PermissionError:
                _time.sleep(0.1 * (i + 1))
        log.warning("kağıt: %s yazılamadı (dosya açık) — sonraki adımda tekrar", self.dosya.name)

    def ozet(self) -> dict:
        kapanan = [x for x in self.islemler if x.durum == "kapandi"]
        return {"acik": self.acik, "durdu": self.durdu, "usd": self.usd, "hedef": self.hedef, "hedef2": self.hedef2, "stop": self.stop,
                "kar_usd": round(sum(x.kar_usd or 0 for x in kapanan), 2),
                "islemler": [{**asdict(x), "kar_yuzde": x.kar_yuzde, "kar_usd": x.kar_usd,
                                "cikis_fiyat": x.cikis_fiyat, "satilan": x.satilan} for x in self.islemler]}

    # ---- akış ----
    def adim(self, sinyaller: list[G.GeriSinyal], son_kapanis: dict[str, tuple[int, float]], simdi_ny: datetime) -> None:
        """Takipçinin her taramasında çağrılır. son_kapanis: tam sembol → (dakika, son KAPANMIŞ 1 dk kapanışı)."""
        if not self.acik:
            return
        uzatilmis = not (REG_OPEN <= simdi_ny.time() < REG_CLOSE)
        try:
            self._yeni_emirler(sinyaller, uzatilmis, simdi_ny)
            for x in self.islemler:
                self._ilerlet(x, son_kapanis.get(x.sym), simdi_ny, uzatilmis)
        except (requests.RequestException, RuntimeError) as e:
            log.warning("kağıt hesap: %s", e)
        self.kaydet()

    def _yeni_emirler(self, sinyaller: list[G.GeriSinyal], uzatilmis: bool, simdi_ny: datetime | None = None) -> None:
        if simdi_ny is not None and simdi_ny.time() >= NY_KAPAT:
            return                                        # gün sonu kapanışından sonra alınan hemen satılırdı → sadece maliyet
        kayitli = {(x.sym, x.al_dk) for x in self.islemler}
        zarar = sum(x.kar_usd or 0 for x in self.islemler if x.durum == "kapandi")
        for s in sinyaller:
            if (s.sym, s.al_dk) in kayitli or s.durum != "bekliyor" or s.tur not in ("TV", "PY"):
                continue
            if Y.alma_nedeni(s, None, dolum_var=False):   # gün > %20 veya sinyal 11:30'dan önce
                continue                                  # (kırmızı oranı limit emir verilirken henüz bilinmiyor)
            if _time.time() / 60 - s.al_dk > G.PENCERE_DK - 1:
                continue                                  # geç gelmiş / takipçi geç başlamış
            acik = sum(x.durum in ("emir", "doldu") for x in self.islemler)
            if acik >= self.max_acik:
                log.info("kağıt: %s atlandı (en çok %d açık)", s.sym, self.max_acik)
                continue
            if zarar <= -self.gunluk_zarar:
                self.durdu = f"günlük zarar sınırı ({zarar:.0f} $) — bugün yeni işlem yok"
                return
            qty = math.floor(self.usd / s.seviye)
            if qty < 1:
                continue
            x = Islem(s.sym, s.al_dk, s.seviye, qty)
            x.bid, x.ask = self._kota(_kisa(s.sym))
            try:
                x.emir_id = self._emir(_kisa(s.sym), qty, "buy", s.seviye, uzatilmis)
                x.emir_zaman = _time.time()
                log.info("🧾 KAĞIT alış emri %s %d adet @ %s", s.sym, qty, _fiyat(s.seviye))
            except RuntimeError as e:
                x.durum, x.hata = "hata", str(e)[:200]
                log.warning("kağıt emir hatası %s: %s", s.sym, e)
            self.islemler.append(x)

    def _ilerlet(self, x: Islem, mum: tuple[int, float] | None, simdi_ny: datetime, uzatilmis: bool) -> None:
        sym = _kisa(x.sym)
        # stop sadece dolumdan SONRA kapanan mumla
        kapanis = mum[1] if mum and x.dolum_zaman and mum[0] * 60 >= x.dolum_zaman - 60 else None
        if x.durum == "emir":
            self._alis_kontrol(x, sym)
            return
        if x.durum != "doldu":
            return
        if x.cikis_id:                                    # stop / gün sonu satışı gönderildi
            o = self._istek("GET", f"/orders/{x.cikis_id}")
            if o["status"] == "filled":
                x.satislar.append({"qty": int(float(o["filled_qty"])), "fiyat": float(o["filled_avg_price"]),
                                   "neden": x.cikis_neden})
                self._kapat(x)
            elif o["status"] in ("canceled", "expired", "rejected"):
                x.cikis_id = None                         # tekrar denenecek
            return
        self._bacak_kontrol(x)
        if x.satilan >= x.qty:
            x.cikis_neden = "hedef"
            self._kapat(x)
            return
        neden = None
        if kapanis is not None and kapanis < x.dolum_fiyat * (1 - self.stop / 100):
            neden = "stop"
        elif simdi_ny.time() >= NY_KAPAT:
            neden = "gün sonu"
        if neden:
            self._bacaklari_iptal(x)
            kalan = x.qty - x.satilan
            x.cikis_neden = neden
            if kalan <= 0:
                self._kapat(x)
                return
            fiyat = None if not uzatilmis else (kapanis or x.dolum_fiyat) * 0.97   # premarket: sadece limit
            x.cikis_id = self._emir(sym, kalan, "sell", fiyat, uzatilmis)
            log.info("🧾 KAĞIT %s satış emri %s (%d adet)", neden, x.sym, kalan)

    def _alis_kontrol(self, x: Islem, sym: str) -> None:
        o = self._istek("GET", f"/orders/{x.emir_id}")
        if o["status"] == "filled":
            x.durum, x.dolum_fiyat, x.dolum_zaman = "doldu", float(o["filled_avg_price"]), _time.time()
            x.qty1 = x.qty // 2 if x.qty >= 2 else x.qty
            x.hedef_id = self._emir(sym, x.qty1, "sell", x.dolum_fiyat * (1 + self.hedef / 100), True, yukari=True)
            if x.qty - x.qty1 > 0:
                x.hedef2_id = self._emir(sym, x.qty - x.qty1, "sell", x.dolum_fiyat * (1 + self.hedef2 / 100), True,
                                         yukari=True)
            log.info("🧾 KAĞIT doldu %s @ %.4g → yarısı +%%%g, yarısı +%%%g", x.sym, x.dolum_fiyat, self.hedef,
                     self.hedef2)
        elif o["status"] in ("canceled", "expired", "rejected"):
            x.durum = "iptal"
        elif _time.time() - (x.emir_zaman or 0) > G.PENCERE_DK * 60:
            self._istek("DELETE", f"/orders/{x.emir_id}")
            x.durum = "iptal"
            log.info("kağıt: %s 30 dk'da dolmadı, emir iptal", x.sym)

    def _bacak_kontrol(self, x: Islem) -> None:
        for alan, neden in (("hedef_id", "hedef1"), ("hedef2_id", "hedef2")):
            oid = getattr(x, alan)
            if not oid:
                continue
            o = self._istek("GET", f"/orders/{oid}")
            if o["status"] == "filled":
                x.satislar.append({"qty": int(float(o["filled_qty"])), "fiyat": float(o["filled_avg_price"]),
                                   "neden": neden})
                setattr(x, alan, None)
                if neden == "hedef1":
                    x.yari_zaman = _time.time()
                    log.info("🧾 KAĞIT %s yarısı +%%%g'de satıldı, kalan içeride", x.sym, self.hedef)

    def _bacaklari_iptal(self, x: Islem) -> None:
        """Açık hedef emirlerini iptal eder; iptalden önce kısmen dolan adetleri kaydeder."""
        for alan, neden in (("hedef_id", "hedef1"), ("hedef2_id", "hedef2")):
            oid = getattr(x, alan)
            if not oid:
                continue
            self._istek("DELETE", f"/orders/{oid}")
            o = self._istek("GET", f"/orders/{oid}") or {}
            dolan = int(float(o.get("filled_qty") or 0))
            if dolan > 0:
                x.satislar.append({"qty": dolan, "fiyat": float(o["filled_avg_price"]), "neden": neden})
            setattr(x, alan, None)

    def _kapat(self, x: Islem) -> None:
        x.durum, x.cikis_zaman = "kapandi", _time.time()
        log.info("🧾 KAĞIT kapandı %s %s %+.1f%% (%+.0f $)", x.sym, x.cikis_neden, x.kar_yuzde or 0, x.kar_usd or 0)


def _kisa(sym: str) -> str:
    return sym.split(":")[-1]
