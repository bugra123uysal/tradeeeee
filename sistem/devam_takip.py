"""Devam eden yükseliş takipçisi — AL sinyalinden 2–4 mum sonra yükseliş sürüyorsa bildirir.

Kullanıcı fikri (2 Kas 2026, CHOW 30 Eki örneği): Swing 5dk AL verdikten sonra 2–4 mum içinde
yükselmeye devam eden hisse iyi giriştir.

Veri (2 Kas 2026'da denendi):
  * TradingView tarayıcısının "|1" gösterge alanları premarket'te DONUK (önceki seansın son mumu) —
    kullanılamaz. Canlı olan: premarket_close / premarket_volume, seans içinde close / volume.
  * Alpaca ücretsiz plan: SIP 1 dk mumlar 15 dk gecikmeli (IEX premarket'te boş).
  → 1 dk mumları kendimiz kurarız: 10 sn'de bir canlı fiyat/hacim okunur; 15 dk'dan eski kısım Alpaca SIP
    mumlarıyla doldurulur (göstergelerin ısınması için). Göstergeler ve kurallar sistem/sinyal.py
    (backtest/devam.py ile birebir aynı hesap).

    python -m sistem.devam_takip            # NY kapanışına kadar döngü + http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time as _time
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone

import numpy as np
import requests
from dotenv import load_dotenv

from . import geri_al as G
from . import kagit as K
from . import yuksek as Y
from . import yukselme as YU
from . import bildirim as B
from .telegram import Telegram
from . import sinyal as S
from . import tv_alarm as A
from . import tv_cdp
from .liste_yenile import FILTER_DIR, NY, PIYASA, PREMARKET, load_filter, session_for, todays_close

log = logging.getLogger("devam")
CIKTI = FILTER_DIR / "devam_sinyaller.json"
SCAN_URL = "https://scanner.tradingview.com/america/scan"
ALPACA_BARS = "https://data.alpaca.markets/v2/stocks/bars"
KOLONLAR = ["name", "description", "close", "volume", "change", "premarket_close", "premarket_volume",
            "premarket_change", "float_shares_outstanding", "relative_volume_10d_calc"]
MAX_SEMBOL = 150
ALPACA_PARCA = 100            # tek istekte sembol
ALPACA_TAZELE_SN = 60
REG_OPEN = time(9, 30)
REG_CLOSE = time(16, 0)
HEDEF_YUZDE = 10.0            # çıkış önerisi: onay fiyatının +%10'u (backtest/cikis.py)
IZ_YUZDE = 5.0                # veya tepe kapanıştan %5 geri çekilme
ZARAR_SIL_YUZDE = 5.0         # 1 dk kapanış onay fiyatının bu kadar altına inerse siteden silinir.
                              # 2 Kas'ta 45 onayın HEPSİ 1–2 dk içinde onayın altına sarktı (CHOW dahil, sonra +%42);
                              # %5 eşiği CHOW/VCIG/INO'yu tuttu, 31'ini sildi.
# Geri çekilme alışı (backtest: scratchpad geri.py, 43 gün, iki yarıda da artıda, ort +%0,37, %68 kârlı — kanıt değil):
GERI_YUZDE = 8.0              # onaydan sonra onay×(1−%8)'e limit alış
GERI_SURE_DK = 30             # limit bu kadar dakika geçerli
GERI_HEDEF = 5.0              # dolumdan +%5 hedef (yüksek dokununca)
GERI_STOP = 8.0               # dolumdan %8 aşağıda kapanış → stop
# SAT/SÜRE izleme (backtest/sure.py): büyük koşularda işlemlerin %50'si SÜRE ile biter (diğerlerinde %31);
# başarısız AL'larda SAT/STOP'un %43'ü ilk 3, %57'si ilk 5 mumda gelir. Bilgi amaçlı, eleme yapmaz.
SURE_MUM = 20
KISA_SAT_MUM = 5


@dataclass
class Sinyal:
    sym: str
    ad: str
    tur: str                     # BB / ADX
    al_dk: int                   # epoch dakika
    al_fiyat: float
    gun_degisim: float | None
    durum: str = "bekliyor"      # bekliyor / onay / iptal / zarar (onaydan sonra giriş altında kapandı → siteden silinir)
    onay_dk: int | None = None
    onay_fiyat: float | None = None
    son_fiyat: float | None = None
    tepe: float | None = None
    neden: str = ""
    macd_ok: bool | None = None
    vwap_ustu: bool | None = None
    float_m: float | None = None
    rvol: float | None = None
    eklendi: float | None = None  # siteye eklendiği an (epoch sn)
    cikis: str | None = None      # çıkış önerisi metni (iz süren stop tetiklenince)
    cikis_fiyat: float | None = None
    cikis_dk: int | None = None
    sira: int = 0                 # günün kaçıncı AL'ı (1 = ilk)
    gecmis: bool = False          # geriye dönük taramada bulundu (takipçi o an izlemiyordu)
    geri_seviye: float | None = None   # limit alış seviyesi (onay −%8)
    geri_durum: str | None = None      # bekliyor / doldu / hedef / stop / yok (süre doldu)
    geri_dk: int | None = None         # dolum dakikası
    geri_fiyat: float | None = None    # dolum fiyatı
    geri_sonuc_dk: int | None = None   # hedef/stop dakikası
    sat_durum: str | None = None       # devam / sat / sure — Swing 5dk'ya göre AL'dan sonra SAT geldi mi
    sat_mum: int | None = None         # AL'dan beri kaç mum (SAT/STOP geldiyse o ana kadar)
    kisa_sat: int | None = None        # bugün bu hissede AL'dan sonra ≤KISA_SAT_MUM mumda SAT/STOP sayısı (sık AL-SAT)


def _f(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) else None


def guvenli_degistir(tmp: Path, hedef: Path, deneme: int = 5, bekle: float = 0.1) -> bool:
    """Windows'ta site dosyayı okurken os.replace 'Erişim engellendi' verir (4 Kas 15:54'te takipçiyi düşürdü).
    Birkaç kez dener; olmazsa bu turu atlar (bir sonraki adımda yeniden yazılır) — süreç çökmez."""
    for i in range(deneme):
        try:
            tmp.replace(hedef)
            return True
        except PermissionError:
            _time.sleep(bekle * (i + 1))
    log.warning("%s yazılamadı (dosya başka süreçte açık) — sonraki adımda tekrar", hedef.name)
    return False


def tara(port: int) -> list[dict]:
    """Premarket VE piyasa filtreleriyle yükselenler (liste yenileyiciyle aynı filtreler)."""
    satirlar: dict[str, dict] = {}
    for s in (PREMARKET, PIYASA):
        q = {k: v for k, v in load_filter(s).items() if not k.startswith("_")}
        q["columns"] = KOLONLAR
        q["range"] = [0, MAX_SEMBOL]
        res = tv_cdp.request(SCAN_URL, "POST", q, text_plain=True, port=port)
        for row in res.get("data", []):
            satirlar.setdefault(row["s"], {"s": row["s"], **dict(zip(KOLONLAR, row["d"]))})
    return list(satirlar.values())


class Mumlar:
    """Bir sembolün 1 dk mumları: {epoch dakika: [o, h, l, c, v]} — Alpaca (gecikmeli) + canlı."""

    def __init__(self) -> None:
        self.bar: dict[int, list[float]] = {}
        self.canli_dk: set[int] = set()            # canlı fiyatlardan kurulan (henüz Alpaca'sı gelmemiş) dakikalar
        self._bas_hacim: dict[int, float] = {}     # dakikanın ilk görülen kümülatif günlük hacmi

    def alpaca_ekle(self, dk: int, o: float, h: float, l: float, c: float, v: float) -> None:
        self.bar[dk] = [o, h, l, c, v]             # gerçek SIP mumu canlı tahmini ezer
        self.canli_dk.discard(dk)

    def fiyat_ekle(self, dk: int, p: float, kum_hacim: float | None) -> None:
        b = self.bar.get(dk)
        if b is not None and dk not in self.canli_dk:
            return                                 # Alpaca mumu var — dokunma
        if b is None:
            self.bar[dk] = b = [p, p, p, p, 0.0]
            self.canli_dk.add(dk)
        b[1], b[2], b[3] = max(b[1], p), min(b[2], p), p
        if kum_hacim is not None:
            bas = self._bas_hacim.setdefault(dk, kum_hacim)
            b[4] = max(kum_hacim - bas, 0.0)

    def diziler(self, son_dk: int) -> tuple[np.ndarray, ...]:
        """son_dk dahil kapanmış mumlar → (dakikalar, o, h, l, c, v)."""
        dks = sorted(d for d in self.bar if d <= son_dk)
        a = np.array([self.bar[d] for d in dks], float).reshape(-1, 5)
        return (np.array(dks), a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4])


def alpaca_mumlar(semboller: list[str], bas: datetime, zaman: str = "1Min") -> dict[str, list[dict]]:
    key, secret = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not (key and secret) or not semboller:
        return {}
    h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    out: dict[str, list[dict]] = {}
    for i in range(0, len(semboller), ALPACA_PARCA):
        parca = semboller[i:i + ALPACA_PARCA]
        tok = None
        while True:
            p = {"symbols": ",".join(parca), "timeframe": zaman, "start": bas.isoformat(), "limit": 10000,
                 "adjustment": "split", "feed": "sip"}
            if tok:
                p["page_token"] = tok
            r = requests.get(ALPACA_BARS, params=p, headers=h, timeout=30)
            r.raise_for_status()
            j = r.json()
            for sym, bars in (j.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            tok = j.get("next_page_token")
            if not tok:
                break
    return out


def gunun_al_lari(dks, o, h, l, c, v, gun_bas_dk: int) -> list[tuple[str, int, float, int]]:
    """Günün (04:00 NY'den beri) TÜM AL sinyalleri → [(tur, dk, fiyat, günün kaçıncı AL'ı)].
    Takipçi geç başlasa ya da hisse listeye geç girse de günün ilk AL'ından itibaren incelenir."""
    if len(c) < S.EMA_LEN:
        return []
    g = S.gostergeler(o, h, l, c, v)
    bugun = [(i, tur) for i, tur in S.al_sinyalleri(c, g) if dks[i] >= gun_bas_dk]
    return [(tur, int(dks[i]), float(c[i]), n + 1) for n, (i, tur) in enumerate(bugun)]


def onceki_kapanis(dks, c, gun_bas_dk: int) -> float | None:
    """Önceki seansın 16:00 NY kapanışı (son 16:00 öncesi mumun kapanışı, gece seansı hariç)."""
    for i in range(len(dks) - 1, -1, -1):
        if dks[i] >= gun_bas_dk:
            continue
        if datetime.fromtimestamp(int(dks[i]) * 60, NY).time() < REG_CLOSE:
            return float(c[i])
    return None


def onceki_tarayicidan(row: dict, premarket: bool) -> float | None:
    """Önceki seans kapanışı: premarket'te 'close' zaten önceki kapanıştır; seansta close / (1 + change%)."""
    c, ch = _f(row.get("close")), _f(row.get("change"))
    if not c:
        return None
    if premarket:
        return c
    return c / (1 + ch / 100) if ch is not None and ch > -100 else None


def onay_durumu(sig: Sinyal, dks, o, h, l, c, v, min_artis: float) -> tuple[str, str]:
    """sinyal.onay_mumu kuralı, mum sayısı DAKİKA farkıyla (seyrek mumlarda da 2–4 dk)."""
    g = S.gostergeler(o, h, l, c, v)
    svwap = np.cumsum(c * v) / np.maximum(np.cumsum(v), 1)
    for j in np.where(dks > sig.al_dk)[0]:
        k = int(dks[j] - sig.al_dk)
        if k > S.ONAY_MAX:
            return "iptal", f"{S.ONAY_MAX} mumda onay gelmedi"
        if c[j] < sig.al_fiyat:
            return "iptal", "AL kapanışının altına indi"
        if c[j] < g.vw[j] and c[j] < g.e50[j]:
            return "iptal", "VWMA20 ve EMA50 altına indi (SAT)"
        artis = (c[j] / sig.al_fiyat - 1) * 100
        if k >= S.ONAY_MIN and artis >= min_artis and c[j] >= o[j]:
            sig.onay_dk, sig.onay_fiyat = int(dks[j]), float(c[j])
            sig.macd_ok = bool(g.macd[j] > g.macd_sig[j]) if not np.isnan(g.macd_sig[j]) else None
            sig.vwap_ustu = bool(c[j] > svwap[j])
            return "onay", f"{k}. mumda +%{artis:.1f}"
    return "bekliyor", ""


def geri_cekilme(sig: Sinyal, dks, o, h, l, c) -> None:
    """Onaydan sonra GERI_SURE_DK içinde düşük onay×(1−GERI_YUZDE) seviyesine inerse "doldu";
    sonra +GERI_HEDEF (yüksek) → "hedef", dolumdan GERI_STOP aşağıda kapanış → "stop". Backtest ile aynı sıra."""
    sig.geri_seviye = round(sig.onay_fiyat * (1 - GERI_YUZDE / 100), 6)
    if sig.geri_durum in ("hedef", "stop"):
        return
    m = dks > sig.onay_dk
    d, o2, h2, l2, c2 = dks[m], o[m], h[m], l[m], c[m]
    if sig.geri_durum in (None, "bekliyor"):
        icinde = np.where((d - sig.onay_dk <= GERI_SURE_DK) & (l2 <= sig.geri_seviye))[0]
        if not len(icinde):
            gecti = len(d) and int(d[-1]) - sig.onay_dk >= GERI_SURE_DK
            sig.geri_durum = "yok" if gecti else "bekliyor"
            return
        k = int(icinde[0])
        sig.geri_durum, sig.geri_dk = "doldu", int(d[k])
        sig.geri_fiyat = float(min(sig.geri_seviye, o2[k]))
        log.info("↩ GERİ ÇEKİLME %s: %.4g seviyesine indi (onay %.4g)", sig.sym, sig.geri_fiyat, sig.onay_fiyat)
    if sig.geri_durum != "doldu":
        return
    k0 = int(np.where(d == sig.geri_dk)[0][0]) if sig.geri_dk in d else None
    if k0 is None:
        return
    hedef, stop = sig.geri_fiyat * (1 + GERI_HEDEF / 100), sig.geri_fiyat * (1 - GERI_STOP / 100)
    for k in range(k0, len(d)):
        if k > k0 and h2[k] >= hedef:
            sig.geri_durum, sig.geri_sonuc_dk = "hedef", int(d[k])
        elif c2[k] < stop:
            sig.geri_durum, sig.geri_sonuc_dk = "stop", int(d[k])
        else:
            continue
        log.info("↩ %s geri çekilme alışı: %s", sig.sym, sig.geri_durum)
        return


def sat_bul(al_i: int, c, g: S.Gostergeler, dks) -> tuple[str, int]:
    """Swing 5dk çıkışı: AL'dan sonra ilk SAT (VWMA20 & EMA50 altı) / STOP (AL − 1,5 ATR) → ("sat", mum);
    SURE_MUM boyunca yoksa ("sure", SURE_MUM); veri bitti → ("devam", mum). Mum = dakika farkı."""
    stop = c[al_i] - 1.5 * g.atr[al_i]
    for k in range(al_i + 1, len(c)):
        mum = int(dks[k] - dks[al_i])
        if mum > SURE_MUM:
            return "sure", SURE_MUM
        if (c[k] < g.vw[k] and c[k] < g.e50[k]) or c[k] < stop:
            return "sat", mum
    return "devam", int(dks[-1] - dks[al_i])


def sat_izle(sinyaller: list[Sinyal], dks, o, h, l, c, v, gun_bas_dk: int) -> None:
    """Onaylı sinyallere SAT/SÜRE durumu ve hissenin bugünkü 'sık AL-SAT' sayısını yazar."""
    if not sinyaller or len(c) < S.EMA_LEN:
        return
    g = S.gostergeler(o, h, l, c, v)
    idx = {int(d): i for i, d in enumerate(dks)}
    kisa = 0
    for i, _ in S.al_sinyalleri(c, g):
        if dks[i] >= gun_bas_dk:
            durum, mum = sat_bul(i, c, g, dks)
            kisa += durum == "sat" and mum <= KISA_SAT_MUM
    for sig in sinyaller:
        i = idx.get(sig.al_dk)
        if i is not None:
            sig.sat_durum, sig.sat_mum = sat_bul(i, c, g, dks)
        sig.kisa_sat = kisa


def onay_sonrasi(sig: Sinyal, dks, o, h, l, c) -> None:
    """Onaydan sonraki kapanmış mumlar.
    * geri çekilme alışı izlenir (geri_cekilme). Geri çekilme penceresi açıkken ya da alış dolmuşken
      kart silinmez; alış stop olursa silinir.
    * diğerlerinde kapanış onay fiyatının ZARAR_SIL_YUZDE altında → "zarar", siteden silinir.
    * çıkış önerisi (bir kez, geri çekilme alışı yoksa): +HEDEF_YUZDE veya tepeden IZ_YUZDE."""
    m = dks > sig.onay_dk
    sonra_c, sonra_h, sonra_dk = c[m], h[m], dks[m]
    if not len(sonra_c):
        return
    geri_cekilme(sig, dks, o, h, l, c)
    if sig.geri_durum == "stop":
        sig.durum, sig.neden = "zarar", f"geri çekilme alışı %{GERI_STOP:g} stop oldu"
        log.info("zarar — siteden çıktı %s (geri çekilme stop)", sig.sym)
        return
    if sig.geri_durum in ("bekliyor", "doldu", "hedef"):
        return
    pencere_sonrasi = sonra_c[sonra_dk - sig.onay_dk > GERI_SURE_DK]
    if len(pencere_sonrasi) and pencere_sonrasi.min() < sig.onay_fiyat * (1 - ZARAR_SIL_YUZDE / 100):
        sig.durum, sig.neden = "zarar", f"onay fiyatının %{ZARAR_SIL_YUZDE:g} altında kapandı"
        log.info("zarar — siteden çıktı %s", sig.sym)
        return
    if sig.cikis is not None:
        return
    hedef = sig.onay_fiyat * (1 + HEDEF_YUZDE / 100)
    tepe = np.maximum.accumulate(sonra_c)
    for k in range(len(sonra_c)):
        if sonra_h[k] >= hedef:
            px, metin = hedef, f"ÇIK: +%{HEDEF_YUZDE:g} hedefe ulaştı"
        elif sonra_c[k] < tepe[k] * (1 - IZ_YUZDE / 100):
            px, metin = float(sonra_c[k]), f"ÇIK: tepeden %{IZ_YUZDE:g} geri çekildi"
        else:
            continue
        kar = (px / sig.onay_fiyat - 1) * 100
        sig.cikis, sig.cikis_fiyat, sig.cikis_dk = f"{metin} ({kar:+.1f}%)", float(px), int(sonra_dk[k])
        log.info("%s %s", sig.sym, sig.cikis)
        return


class Takipci:
    def __init__(self, port: int, min_artis: float):
        self.port, self.min_artis = port, min_artis
        self.mumlar: dict[str, Mumlar] = {}
        self.bilgi: dict[str, dict] = {}
        self.sinyaller: list[Sinyal] = []
        self.geri: list[G.GeriSinyal] = []          # geri çekilme AL'ı (sistem/geri_al.py)
        self.yuksek: list[G.GeriSinyal] = []        # 'yüksek' sinyalleri — TradingView alarmlarından (tv_alarm)
        self.onceki: dict[str, float] = {}          # önceki seans kapanışı (tarayıcıdan)
        self.gun = datetime.now(NY).date()
        self.son_kapanan_dk = int(_time.time() // 60) - 1
        self.son_alpaca = 0.0
        self.gun_bas_dk = int(datetime.now(NY).replace(hour=4, minute=0, second=0, microsecond=0).timestamp() // 60)
        self._yukle()
        self.kagit = K.KagitHesap(FILTER_DIR / "kagit.json")   # Alpaca KAĞIT hesap (sahte para), sistem/kagit.py
        self.yukselme = YU.Adaylar(port)            # TradingView "yükselme-adayları" tarayıcısı + giriş şartları
        self.kagit.yukle(str(self.gun))
        tg = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", ""))
        self.bildirici = B.Bildirici(tg.send, FILTER_DIR / "telegram_gonderilen.json", str(self.gun))

    def _yukle(self) -> None:
        try:
            d = json.loads(CIKTI.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if d.get("gun") == str(self.gun):
            self.sinyaller = [Sinyal(**x) for x in d.get("sinyaller", [])]
            self.onceki = {k: float(v) for k, v in (d.get("onceki") or {}).items()}
            self.geri = [G.GeriSinyal(**{k: v for k, v in x.items() if k not in ("hedef", "stop")})
                         for x in d.get("geri", [])]
            self.yuksek = [G.GeriSinyal(**{k: v for k, v in x.items() if k not in ("hedef", "stop", "hedef2")})
                           for x in d.get("yuksek", []) if x.get("tur") == "TV"]  # eski Python sinyalleri atılır
            sayac: dict[str, int] = {}
            for x in sorted(self.sinyaller, key=lambda x: x.al_dk):
                sayac[x.sym] = x.sira = sayac.get(x.sym, 0) + 1
            for s in self.sinyaller:          # geri çekilme kuralı gelmeden silinenler yeniden değerlendirilsin
                if s.durum == "zarar" and s.geri_durum is None:
                    s.durum, s.neden, s.cikis, s.cikis_fiyat, s.cikis_dk = "onay", "", None, None, None
            for s in self.sinyaller:          # alan yokken onaylananlar: onay mumu kapanınca eklenmişti
                if s.durum == "onay" and s.eklendi is None and s.onay_dk:
                    s.eklendi = (s.onay_dk + 1) * 60.0

    def kaydet(self) -> None:
        d = {"gun": str(self.gun), "guncel": datetime.now(NY).isoformat(timespec="seconds"),
             "min_artis": self.min_artis, "izlenen": len(self.mumlar),
             "sinyaller": [asdict(s) for s in self.sinyaller],
             "geri_kural": {"geri": G.GERI, "pencere_dk": G.PENCERE_DK, "hedef": G.HEDEF, "stop": G.STOP,
                            "min_gun": G.MIN_GUN},
             "geri": [{**asdict(x), "hedef": x.hedef, "stop": x.stop} for x in self.geri],
             "yuksek_kural": {"gun_min": Y.GUN_MIN, "gun_max": Y.GUN_MAX, "mom": Y.MOM_MIN, "hacim": Y.HACIM_KAT,
                              "bbw": Y.BBW_MIN, "gunluk_max": Y.GUNLUK_MAX,
                              "filtre_gun_max": Y.FILTRE_GUN_MAX, "hedef1": Y.HEDEF1, "hedef2": Y.HEDEF2},
             "yuksek_kaynak": "tradingview_alarm",
             "yuksek": [{**asdict(x), "hedef": x.hedef, "stop": x.stop,
                         "hedef2": x.dolum_fiyat * (1 + Y.HEDEF2 / 100) if x.dolum_fiyat else None}
                        for x in self.yuksek],
             "onceki": self.onceki, "kagit": self.kagit.ozet() if hasattr(self, "kagit") else None,
             "yukselme": self.yukselme.ozet() if hasattr(self, "yukselme") else None}
        tmp = CIKTI.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        guvenli_degistir(tmp, CIKTI)

    def adim(self) -> None:
        simdi = _time.time()
        dk = int(simdi // 60)
        premarket = datetime.now(NY).time() < REG_OPEN
        yeni = []
        for row in tara(self.port) + self.yukselme.tara_guncelle():
            p = _f(row.get("premarket_close" if premarket else "close"))
            if p is None:
                continue
            sym = row["s"]
            if sym not in self.mumlar:
                self.mumlar[sym] = Mumlar()
                yeni.append(sym)
            self.bilgi[sym] = row
            pc = onceki_tarayicidan(row, premarket)
            if pc:
                self.onceki[sym] = pc
            self.mumlar[sym].fiyat_ekle(dk, p, _f(row.get("premarket_volume" if premarket else "volume")))
        self._alarmlari_al()
        izle = {s.sym for s in self.sinyaller if s.durum == "onay"} |                {x.sym for x in self.geri + self.yuksek if x.durum in ("bekliyor", "al", "yarim")}
        for sym in izle - set(self.mumlar):  # yeniden başlatmada tarayıcıda olmayanlar da izlensin
            self.mumlar[sym] = Mumlar()
            yeni.append(sym)
        self._alpaca_doldur(yeni, simdi)
        if dk - 1 > self.son_kapanan_dk:
            for sym in list(self.mumlar):
                self._dakika_kapandi(sym, dk - 1)
            self.son_kapanan_dk = dk - 1
            self._yukselme_degerlendir(dk)
        self._telegram()
        self._guncelle_fiyatlar()
        self._kagit_adim(dk)
        self.kaydet()

    def _telegram(self) -> None:
        """GİRİLECEK HİSSELER ve Yükselme Adayları → Telegram (her olay bir kez)."""
        try:
            self.bildirici.yuksek(self.yuksek)
            self.bildirici.yukselme(list(self.yukselme.adaylar.values()))
        except (TypeError, ValueError, KeyError, AttributeError) as e:
            log.warning("telegram bildirimi hazırlanamadı: %s", e)

    def _yukselme_degerlendir(self, dk: int) -> None:
        """Yükselme adaylarını kapanmış mumlarla değerlendirir (haber · ilk 5 dk · premarket tepesi · hacim · Pine)."""
        for a in self.yukselme.adaylar.values():
            m = self.mumlar.get(a.sym)
            if m is None or not m.bar:
                continue
            dks, o, h, l, c, v = m.diziler(dk - 1)
            pc = self._onceki(a.sym, dks, c) if len(c) else None
            try:
                YU.degerlendir(a, dks, o, h, l, c, v, self.gun_bas_dk, pc, dk)
                tv = [x for x in self.yuksek if x.sym == a.sym and x.tur == "TV"]
                if tv:                                   # gerçek Pine alarmı varsa o geçerli
                    YU.tv_alarmindan(a, min(tv, key=lambda x: x.al_dk))
            except (ValueError, IndexError) as e:
                log.warning("yükselme %s değerlendirilemedi: %s", a.sym, e)

    def _kagit_adim(self, dk: int) -> None:
        son: dict[str, tuple[int, float]] = {}
        for x in self.yuksek:
            m = self.mumlar.get(x.sym)
            kapanmis = [d for d in (m.bar if m else {}) if d <= dk - 1]
            if kapanmis:
                son[x.sym] = (max(kapanmis), m.bar[max(kapanmis)][3])
        self.kagit.adim(self.yuksek, son, datetime.now(NY))

    def _alpaca_doldur(self, yeni: list[str], simdi: float) -> None:
        """Yeni sembollere dünden itibaren, hepsine her dakika son 25 dk SIP mumu (15 dk gecikmeli)."""
        try:
            if yeni:
                bas = datetime.now(NY).replace(hour=4, minute=0, second=0, microsecond=0) - timedelta(days=1)
                self._alpaca_yaz(alpaca_mumlar([s.split(":")[1] for s in yeni], bas))
            if simdi - self.son_alpaca >= ALPACA_TAZELE_SN:
                bas = datetime.now(timezone.utc) - timedelta(minutes=25)
                self._alpaca_yaz(alpaca_mumlar([s.split(":")[1] for s in self.mumlar], bas))
                self.son_alpaca = simdi
        except (requests.RequestException, ValueError) as e:
            log.warning("Alpaca mumları alınamadı: %s", e)

    def _alpaca_yaz(self, veri: dict[str, list[dict]]) -> None:
        tam = {s.split(":")[1]: s for s in self.mumlar}
        for kisa, bars in veri.items():
            m = self.mumlar.get(tam.get(kisa, ""))
            if m is None:
                continue
            for b in bars:
                t = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                m.alpaca_ekle(int(t.timestamp() // 60), b["o"], b["h"], b["l"], b["c"], b["v"])

    def _dakika_kapandi(self, sym: str, dk: int) -> None:
        m = self.mumlar[sym]
        if not m.bar:
            return
        dks, o, h, l, c, v = m.diziler(dk)
        if dk not in m.bar:              # tarayıcıdan düştü: sadece onaylıları (gecikmeli Alpaca mumlarıyla) izle
            onaylilar = [s for s in self.sinyaller if s.sym == sym and s.durum == "onay"]
            for s in onaylilar:
                onay_sonrasi(s, dks, o, h, l, c)
            sat_izle([s for s in onaylilar if s.durum == "onay"], dks, o, h, l, c, v, self.gun_bas_dk)
            self._geri_isle(sym, dks, o, h, l, c, v)
            self._yuksek_isle(sym, dks, o, h, l, c, v)
            return
        self._yeni_al_ekle(sym, dks, o, h, l, c, v)
        for s in [x for x in self.sinyaller if x.sym == sym and x.durum == "bekliyor"]:
            s.durum, s.neden = onay_durumu(s, dks, o, h, l, c, v, self.min_artis)
            if s.durum == "onay":
                s.eklendi = _time.time()
                log.info("✅ ONAY %s AL(%s) %.4g → %.4g (%s)%s", sym, s.tur, s.al_fiyat, s.onay_fiyat, s.neden,
                         " [geriye dönük]" if s.gecmis else "")
            elif s.durum == "iptal":
                log.debug("iptal %s: %s", sym, s.neden)
        onaylilar = [s for s in self.sinyaller if s.sym == sym and s.durum == "onay"]
        for s in onaylilar:
            onay_sonrasi(s, dks, o, h, l, c)
        sat_izle([s for s in onaylilar if s.durum == "onay"], dks, o, h, l, c, v, self.gun_bas_dk)
        self._geri_isle(sym, dks, o, h, l, c, v)
        self._yuksek_isle(sym, dks, o, h, l, c, v)

    def _alarmlari_al(self) -> None:
        """TradingView'de tetiklenen 'yüksek: SİNYAL' alarmlarını siteye ekler (sinyal sadece buradan gelir)."""
        try:
            gelenler = A.bugunku_sinyaller(self.gun, self.port)
        except tv_cdp.TVError as e:
            log.warning("TradingView alarm kaydı: %s", e)
            return
        kayitli = {(x.sym, x.al_dk) for x in self.yuksek}
        for a in gelenler:
            if (a.sym, a.al_dk) in kayitli:
                continue
            pc = self.onceki.get(a.sym)
            row = self.bilgi.get(a.sym, {})
            x = G.GeriSinyal(a.sym, str(row.get("description") or ""), "TV", a.al_dk, a.fiyat,
                             (a.fiyat / pc - 1) * 100 if pc else None, seviye=a.seviye, eklendi=_time.time(),
                             gecmis=_time.time() - a.tetik > 120)
            self.yuksek.append(x)
            kayitli.add((a.sym, a.al_dk))      # tarayıcıda yoksa adim() 'izle' ile mum + Alpaca geçmişi ekler
            log.info("🟣 YÜKSEK (TV alarmı) %s @ %.4g · geri çekilme %.4g", a.sym, a.fiyat, a.seviye)

    def _yuksek_isle(self, sym: str, dks, o, h, l, c, v) -> None:
        """TV alarmından gelen 'yüksek' sinyali → geri çekilme alışı → hedef / stop."""
        simdi = _time.time()
        for x in self.yuksek:
            if x.sym == sym and x.gun_degisim is None:
                pc = self._onceki(sym, dks, c)
                x.gun_degisim = (x.al_fiyat / pc - 1) * 100 if pc else None
        for x in self.yuksek:
            if x.sym != sym:
                continue
            sonra = h[dks > x.al_dk]
            if len(sonra):
                x.tepe = max(x.tepe or 0.0, float(sonra.max()))
            yeni = Y.degerlendir(x, dks, o, h, l, c,       # yarı-yarı çıkış; 16:00 NY'de kapanır (Pine / kağıt gibi)
                                 seans_son_dk=Y.seans_sonu_dk(x.al_dk), simdi_dk=int(simdi // 60))
            if x.dolum_dk is not None and x.al_bildirim is None:
                x.al_bildirim = simdi
                log.info("🟢 TAMAM — ŞARTLAR SAĞLANIYOR, GİR %s @ %.4g", sym, x.dolum_fiyat)
            if x.yari_dk is not None and x.yari_bildirim is None:
                x.yari_bildirim = simdi
                log.info("🟣 %s yarısı +%%%g'de satıldı, kalan içeride", sym, Y.HEDEF1)
            if yeni in ("hedef", "stop"):
                log.info("🟣 %s: %s", sym, yeni)
            elif yeni == "seans":
                log.info("🟣 %s: seans sonu (16:00) — kalan %.4g'den kapandı", sym, x.cikis_fiyat)

    def _onceki(self, sym: str, dks, c) -> float | None:
        return self.onceki.get(sym) or onceki_kapanis(dks, c, self.gun_bas_dk)

    def _geri_isle(self, sym: str, dks, o, h, l, c, v) -> None:
        """Pine AL'ı (gün ≥ %10) → geri çekilme bekle → 'GERİ ÇEKİLME TAMAM — AL' → hedef / stop."""
        if len(c) < S.EMA_LEN:
            return
        g = S.gostergeler(o, h, l, c, v)
        kayitli = {x.al_dk for x in self.geri if x.sym == sym}
        pc = self._onceki(sym, dks, c)
        son_dk, simdi = int(dks[-1]), _time.time()
        for i, tur in G.pine_al_lari(dks, c, g, self.gun_bas_dk):
            al_dk = int(dks[i])
            gun = (c[i] / pc - 1) * 100 if pc else None
            if al_dk in kayitli or gun is None or gun < G.MIN_GUN:
                continue
            row = self.bilgi.get(sym, {})
            self.geri.append(G.GeriSinyal(sym, str(row.get("description") or ""), tur, al_dk, float(c[i]), gun,
                                          seviye=round(float(c[i]) * (1 - G.GERI / 100), 6), eklendi=simdi,
                                          gecmis=son_dk - al_dk > 1))
        for x in self.geri:
            if x.sym != sym:
                continue
            yeni = G.degerlendir(x, dks, o, h, l, c)
            if yeni == "al":
                x.al_bildirim = x.al_bildirim or simdi
                log.info("🟢 GERİ ÇEKİLME TAMAM — AL %s @ %.4g (AL %.4g)%s", sym, x.dolum_fiyat, x.al_fiyat,
                         " [geriye dönük]" if x.gecmis else "")
            elif yeni in ("hedef", "stop"):
                log.info("geri çekilme AL'ı %s: %s", sym, yeni)

    def _yeni_al_ekle(self, sym: str, dks, o, h, l, c, v) -> None:
        kayitli = {s.al_dk for s in self.sinyaller if s.sym == sym}
        yeniler = [x for x in gunun_al_lari(dks, o, h, l, c, v, self.gun_bas_dk) if x[1] not in kayitli]
        if not yeniler:
            return
        row = self.bilgi.get(sym, {})
        fl = _f(row.get("float_shares_outstanding"))
        pc = self._onceki(sym, dks, c)
        son_dk = int(dks[-1])
        for tur, al_dk, fiyat, sira in yeniler:
            gecmis = son_dk - al_dk > 1
            self.sinyaller.append(Sinyal(sym, str(row.get("description") or ""), tur, al_dk, fiyat,
                                         (fiyat / pc - 1) * 100 if pc else None,
                                         float_m=fl / 1e6 if fl else None,
                                         rvol=_f(row.get("relative_volume_10d_calc")), sira=sira, gecmis=gecmis))
            if not gecmis:
                log.info("AL(%s) %s @ %.4g (günün %d. AL'ı) — onay bekleniyor", tur, sym, fiyat, sira)
        # sıra: bu hissenin bugünkü tüm AL kayıtları zamana göre (eski kayıtlarda alan yoktu)
        for n, x in enumerate(sorted((s for s in self.sinyaller if s.sym == sym), key=lambda s: s.al_dk), 1):
            x.sira = n
        if any(x[1] < son_dk - 1 for x in yeniler):
            log.info("%s: geriye dönük %d AL eklendi (günün ilk AL'ı %s)", sym, len(yeniler),
                     datetime.fromtimestamp(yeniler[0][1] * 60).strftime("%H:%M"))

    def _guncelle_fiyatlar(self) -> None:
        for s in self.sinyaller:
            m = self.mumlar.get(s.sym)
            if m is None or not m.bar:
                continue
            son = m.bar[max(m.bar)][3]
            s.son_fiyat = son
            if s.durum == "onay":
                s.tepe = max(s.tepe or son, son)
        dk = int(_time.time() // 60)
        for x in self.geri + self.yuksek:
            m = self.mumlar.get(x.sym)
            if m is None or not m.bar:
                continue
            x.son_fiyat = m.bar[max(m.bar)][3]
            if max(m.bar) in m.canli_dk and G.canli_dolum(x, x.son_fiyat, dk):
                x.al_bildirim = _time.time()
                log.info("🟢 GERİ ÇEKİLME TAMAM — AL %s @ %.4g (canlı)", x.sym, x.dolum_fiyat)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aralik", type=int, default=10, help="tarama aralığı (sn)")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--web-port", type=int, default=8765)
    ap.add_argument("--min-artis", type=float, default=float(os.getenv("DEVAM_MIN_ARTIS", "2.0")))
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(FILTER_DIR / "devam_takip.log", encoding="utf-8")])
    load_dotenv(FILTER_DIR.parent / ".env")
    from .devam_web import baslat
    threading.Thread(target=baslat, args=(a.web_port,), daemon=True).start()
    log.info("web: http://127.0.0.1:%d  ·  onay: AL'dan %d–%d mum sonra +%%%.1f", a.web_port, S.ONAY_MIN,
             S.ONAY_MAX, a.min_artis)
    t = Takipci(a.port, a.min_artis)
    close = todays_close(datetime.now(NY).date())
    while True:
        now = datetime.now(NY)
        s = session_for(now, close)
        if s is None and (close is None or now.time() >= close):
            log.info("seans bitti / işlem günü değil — çıkılıyor")
            t.kaydet()
            return
        if s is not None:
            try:
                t.adim()
            except tv_cdp.TVError as e:
                log.warning("%s", e)
        _time.sleep(a.aralik)


if __name__ == "__main__":
    main()
