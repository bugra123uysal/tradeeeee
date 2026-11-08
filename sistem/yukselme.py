"""'yükselme-adayları' — TradingView Hisse Takipçisi'ndeki aynı adlı tarayıcı + kullanıcının giriş şartları (4 Kas 2026).

Tarayıcı sorgusu, TradingView'ın kendi gönderdiği istekten birebir alındı:
  premarket değişimi > %10 · premarket hacmi > 100 bin · float ≤ 20 milyon · fiyat (close) 1–1500 $ (6 Kas 2026: kullanıcı TV'de değiştirdi)
Her aday için şartlar (kullanıcının kuralları):
  1. Haber var mı? (önceki seans kapanışından beri TradingView haber başlığı) — yoksa GEÇ
  2. İlk 5 dakika: 09:30–09:35 NY (17:30–17:35 TR) işlem yok, açılış oynaklığı geçsin
  3. Premarket tepesini kırıyor mu? Kırıp üstünde tutunan → devam adayı; kıramayıp / geri düşen → gap dolma riski
  4. Hacim: kırılım mumunun hacmi önceki HACIM_ONCEKI mumun ortalamasının ≥ HACIM_KAT katı
  + Pine: günün Swing 5dk AL'ları ve 'yüksek' kuralı (sistem/yuksek.py) bu hissede tetiklendi mi
Bilgi amaçlıdır, al-sat tavsiyesi değildir.
"""
from __future__ import annotations

import json
import logging
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from . import geri_al as G
from . import sinyal as S
from . import tv_cdp
from . import yuksek as Y

log = logging.getLogger("yukselme")
NY = ZoneInfo("America/New_York")
SCAN_URL = "https://scanner.tradingview.com/america/scan"
HABER_URL = "https://news-headlines.tradingview.com/v2/view/headlines/symbol?client=web&lang=en&streaming=false&symbol="
KOLONLAR = ["name", "description", "premarket_close", "premarket_change", "premarket_volume", "premarket_high",
            "close", "change", "volume", "float_shares_outstanding_current", "relative_volume_10d_calc"]
SORGU = {
    "markets": ["america"],
    "filter": [{"left": "premarket_change", "operation": "greater", "right": 10},
               {"left": "premarket_volume", "operation": "greater", "right": 100000},
               {"left": "float_shares_outstanding_current", "operation": "eless", "right": 20000000},
               {"left": "close", "operation": "in_range", "right": [1, 1500]}],     # 6 Kas: TV'deki gibi (alt 1 $)
    "filter2": {"operator": "or", "operands": [
        {"operation": {"operator": "and", "operands": [{"expression": {"left": "type", "operation": "equal", "right": "stock"}},
                                                        {"expression": {"left": "typespecs", "operation": "has", "right": ["common"]}}]}},
        {"operation": {"operator": "and", "operands": [{"expression": {"left": "type", "operation": "equal", "right": "dr"}}]}}]},
    "sort": {"sortBy": "premarket_change", "sortOrder": "desc"},
    "range": [0, 100],
}
ACILIS, ILK5_BITIS = (9, 30), (9, 35)
HACIM_KAT = 2.0
HACIM_ONCEKI = 5
HABER_TAZELE_SN = 300


@dataclass
class Aday:
    sym: str
    ad: str = ""
    pm_degisim: float | None = None
    pm_hacim: float | None = None
    pm_tepe: float | None = None
    fiyat: float | None = None
    gun_degisim: float | None = None
    float_m: float | None = None
    rvol: float | None = None
    ilk_gorulme: float | None = None          # listeye girdiği an (epoch sn)
    haber_sayisi: int = 0
    haberler: list = field(default_factory=list)   # [{zaman, kaynak, baslik}]
    asama: str = "premarket"                  # premarket / ilk5 / seans
    kirilim: str = "bekliyor"                 # bekliyor / tutunuyor / geri_dondu / kiramadi
    kirilim_dk: int | None = None
    kirilim_hacim_kat: float | None = None
    swing_al: list = field(default_factory=list)   # [[tur, dk, fiyat]]
    yuksek_dk: int | None = None               # 'yüksek' Pine sinyali (ana sinyal)
    yuksek_fiyat: float | None = None
    yuksek_gun: float | None = None            # sinyal anında gün değişimi %
    yuksek_seviye: float | None = None         # −%GERI geri çekilme alış seviyesi
    yuksek_durum: str | None = None            # bekliyor / doldu / olmadi
    yuksek_dolum_dk: int | None = None
    yuksek_alma: bool = False                  # gün > FILTRE_GUN_MAX → sitede gri "ALMA"
    yuksek_kaynak: str | None = None           # "TV" (TradingView alarmı — gerçek Pine) / "hesap" (1 dk mumlarla)
    karar: str = "İZLE"
    neden: str = ""


def tara(port: int = 9222) -> list[dict]:
    q = {**SORGU, "columns": KOLONLAR}
    res = tv_cdp.request(SCAN_URL, "POST", q, text_plain=True, port=port)
    return [{"s": r["s"], **dict(zip(KOLONLAR, r["d"]))} for r in res.get("data", [])]


def onceki_kapanis_ani(simdi: datetime) -> datetime:
    """Önceki işlem gününün 16:00 NY'si (hafta sonunu atlar). Bundan sonraki haberler 'bugünün haberi'."""
    g = simdi.astimezone(NY)
    bugun16 = g.replace(hour=16, minute=0, second=0, microsecond=0)
    t = bugun16 if g >= bugun16 else bugun16 - timedelta(days=1)
    while t.weekday() >= 5:
        t -= timedelta(days=1)
    return t


def haberler(sym: str, simdi: datetime, port: int = 9222) -> list[dict]:
    js = (f"(async()=>{{try{{const r=await fetch({json.dumps(HABER_URL + sym)});"
          f"return JSON.stringify({{s:r.status,b:await r.text()}})}}catch(e){{return JSON.stringify({{e:String(e)}})}}}})()")
    r = tv_cdp.evaluate(js, port=port)
    if r.get("s") != 200:
        raise tv_cdp.TVError(f"haber alınamadı: {r}")
    sinir = onceki_kapanis_ani(simdi).timestamp()
    out = [{"zaman": int(it.get("published", 0)), "kaynak": it.get("provider") or "", "baslik": it.get("title") or ""}
           for it in json.loads(r["b"]).get("items", []) if it.get("published", 0) >= sinir]
    return sorted(out, key=lambda x: -x["zaman"])


def _dk(gun_bas_dk: int, saat: tuple[int, int]) -> int:
    return gun_bas_dk + (saat[0] - 4) * 60 + saat[1]


def asama(t: datetime) -> str:
    s = t.astimezone(NY)
    hm = (s.hour, s.minute)
    return "premarket" if hm < ACILIS else ("ilk5" if hm < ILK5_BITIS else "seans")


def kirilim_durumu(dks, h, c, v, pm_tepe: float, acilis_dk: int, son_dk: int) -> tuple[str, int | None, float | None]:
    """Açılıştan sonra 1 dk KAPANIŞ premarket tepesinin üstüne çıktı mı, sonra üstünde kaldı mı."""
    seans = np.flatnonzero((dks >= acilis_dk) & (dks <= son_dk))
    if not len(seans):
        return "bekliyor", None, None
    ust = seans[c[seans] > pm_tepe]
    if not len(ust):
        return ("kiramadi" if c[seans[-1]] < pm_tepe else "bekliyor"), None, None
    k = int(ust[0])
    onceki = v[max(0, k - HACIM_ONCEKI):k]
    hk = float(v[k] / onceki.mean()) if len(onceki) and onceki.mean() > 0 else None
    sonra = c[k:seans[-1] + 1]
    durum = "tutunuyor" if (sonra >= pm_tepe).all() else ("geri_dondu" if sonra[-1] < pm_tepe else "tutunuyor")
    return durum, int(dks[k]), hk


def yuksek_durumu(a: Aday, i: int, dks, l, c, onceki: float, simdi_dk: int) -> None:
    """'yüksek' sinyali → sitedeki kural: PENCERE_DK içinde sinyal kapanışı −%GERI'ye inerse alış (doldu)."""
    a.yuksek_kaynak = "hesap"
    a.yuksek_dk, a.yuksek_fiyat = int(dks[i]), float(c[i])
    a.yuksek_gun = float((c[i] / onceki - 1) * 100)          # düz float/bool: JSON'a yazılabilsin
    a.yuksek_seviye = float(c[i]) * (1 - G.GERI / 100)
    a.yuksek_alma = bool(Y.FILTRE_GUN_MAX is not None and a.yuksek_gun > Y.FILTRE_GUN_MAX)
    k = np.flatnonzero((dks > dks[i]) & (dks - dks[i] <= G.PENCERE_DK) & (l <= a.yuksek_seviye))
    if len(k):
        a.yuksek_durum, a.yuksek_dolum_dk = "doldu", int(dks[k[0]])
    else:
        a.yuksek_durum = "olmadi" if simdi_dk - dks[i] > G.PENCERE_DK else "bekliyor"


TV_DURUM = {"bekliyor": "bekliyor", "yok": "olmadi", "al": "doldu", "yarim": "doldu", "hedef": "doldu",
            "stop": "doldu", "seans": "doldu"}


def tv_alarmindan(a: Aday, x) -> None:
    """TradingView'daki gerçek 'yüksek' Pine alarmı (takipçinin yuksek listesi) hesaplananın yerine geçer."""
    a.yuksek_kaynak = "TV"
    a.yuksek_dk, a.yuksek_fiyat, a.yuksek_seviye = int(x.al_dk), float(x.al_fiyat), float(x.seviye)
    a.yuksek_gun = float(x.gun_degisim) if x.gun_degisim is not None else None
    a.yuksek_alma = bool(Y.FILTRE_GUN_MAX is not None and a.yuksek_gun is not None and a.yuksek_gun > Y.FILTRE_GUN_MAX)
    a.yuksek_durum = TV_DURUM.get(x.durum, x.durum)
    a.yuksek_dolum_dk = int(x.dolum_dk) if x.dolum_dk is not None else None


def karar_ver(a: Aday) -> tuple[str, str]:
    if a.haber_sayisi == 0:
        return "GEÇ", "Haber yok — hareketin sebebi bilinmiyor"
    if a.asama == "premarket":
        return "İZLE", "Premarket — açılışı ve ilk 5 dakikayı bekle"
    if a.asama == "ilk5":
        return "BEKLE", "İlk 5 dakika (17:30–17:35 TR) — işlem yok"
    if a.kirilim == "kiramadi":
        return "ZAYIF", "Premarket tepesinin altında — gap dolma riski"
    if a.kirilim == "geri_dondu":
        return "ZAYIF", "Premarket tepesini kırdı ama altına geri düştü — gap dolma riski"
    if a.kirilim == "bekliyor":
        return "İZLE", "Premarket tepesi henüz kırılmadı"
    if a.kirilim_hacim_kat is None or a.kirilim_hacim_kat < HACIM_KAT:
        return "İZLE", "Kırıldı ve tutunuyor ama kırılım mumunda hacim belirgin yüksek değil"
    return "DEVAM ADAYI", "Haber var · ilk 5 dk geçti · premarket tepesi hacimle kırıldı ve üstünde tutunuyor"


def degerlendir(a: Aday, dks, o, h, l, c, v, gun_bas_dk: int, onceki: float | None, simdi_dk: int) -> None:
    acilis, ilk5 = _dk(gun_bas_dk, ACILIS), _dk(gun_bas_dk, ILK5_BITIS)
    a.asama = "premarket" if simdi_dk < acilis else ("ilk5" if simdi_dk < ilk5 else "seans")
    if a.pm_tepe is None and len(dks):
        pm = (dks >= gun_bas_dk) & (dks < acilis)
        a.pm_tepe = float(h[pm].max()) if pm.any() else None
    if a.asama != "premarket" and a.pm_tepe and len(dks):
        a.kirilim, a.kirilim_dk, a.kirilim_hacim_kat = kirilim_durumu(dks, h, c, v, a.pm_tepe, acilis, simdi_dk - 1)
    if len(c) >= S.EMA_LEN:
        g = S.gostergeler(o, h, l, c, v)
        bas = int(np.searchsorted(dks, gun_bas_dk))
        a.swing_al = [[t, int(dks[i]), float(c[i])] for i, t in S.al_sinyalleri(c, g) if i >= bas][-5:]
        if onceki:
            ys = Y.yuksek_sinyalleri(dks, o, h, l, c, v, g, onceki, gun_bas_dk)
            if ys:
                yuksek_durumu(a, ys[0], dks, l, c, onceki, simdi_dk)
    a.karar, a.neden = karar_ver(a)


class Adaylar:
    """Takipçinin her adımında çağrılır: tarar, haberleri (5 dk'da bir) tazeler, mumlarla değerlendirir."""

    def __init__(self, port: int = 9222) -> None:
        self.port = port
        self.adaylar: dict[str, Aday] = {}
        self._haber_zaman: dict[str, float] = {}
        self.son_tarama: str | None = None
        self.hata: str | None = None

    def tara_guncelle(self) -> list[dict]:
        """Listeyi tazeler; tarayıcı satırlarını döner (takipçi bunlardan 1 dk mum kurar)."""
        try:
            satirlar = tara(self.port)
            self.hata = None
        except (tv_cdp.TVError, OSError, ValueError) as e:
            self.hata = f"tarayıcı okunamadı: {e}"
            log.warning(self.hata)
            return []
        simdi = _time.time()
        for r in satirlar:
            a = self.adaylar.setdefault(r["s"], Aday(r["s"], ilk_gorulme=simdi))
            a.ad = str(r.get("description") or "")
            a.pm_degisim, a.pm_hacim = r.get("premarket_change"), r.get("premarket_volume")
            a.fiyat, a.gun_degisim = r.get("close"), r.get("change")
            a.pm_tepe = r.get("premarket_high") or a.pm_tepe
            a.float_m = (r["float_shares_outstanding_current"] / 1e6) if r.get("float_shares_outstanding_current") else None
            a.rvol = r.get("relative_volume_10d_calc")
            if simdi - self._haber_zaman.get(a.sym, 0) >= HABER_TAZELE_SN:
                try:
                    a.haberler = haberler(a.sym, datetime.now(NY), self.port)[:5]
                    a.haber_sayisi = len(a.haberler)
                    self._haber_zaman[a.sym] = simdi
                except (tv_cdp.TVError, OSError, ValueError) as e:
                    log.warning("%s haberleri alınamadı: %s", a.sym, e)
            a.asama = asama(datetime.now(NY))
            a.karar, a.neden = karar_ver(a)          # mum gelmeden de (ör. haber yok → GEÇ) güncel olsun
        self.son_tarama = datetime.now(NY).isoformat(timespec="seconds")
        return satirlar

    def ozet(self) -> dict:
        sira = {"DEVAM ADAYI": 0, "İZLE": 1, "BEKLE": 2, "ZAYIF": 3, "GEÇ": 4}
        liste = sorted(self.adaylar.values(), key=lambda a: (sira.get(a.karar, 9), -(a.pm_degisim or 0)))
        return {"son_tarama": self.son_tarama, "hata": self.hata,
                "kural": {"hacim_kat": HACIM_KAT, "hacim_onceki": HACIM_ONCEKI,
                          "sorgu": "premarket değişimi > %10 · premarket hacmi > 100 bin · float ≤ 20 M · fiyat 1–1500 $"},
                "adaylar": [asdict(a) for a in liste]}
