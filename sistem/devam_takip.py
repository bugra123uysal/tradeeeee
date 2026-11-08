"""Takipçi — TradingView 'yüksek' alarmları + Yükselme Adayları → site (http://127.0.0.1:8765) ve Telegram.

  * 'yüksek' sinyali SADECE TradingView alarmından gelir (sistem/tv_alarm.py); geri çekilme alışı / yarı-yarı çıkış
    sistem/yuksek.py'de izlenir. GİR sadece sinyal sonrası seçimden geçenlere (yuksek.alma_nedeni: gün ≤ %20 ·
    sinyal ≥ 11:30 · sakin geri çekilme), diğerleri gri "ALMA".
  * Yükselme Adayları: sistem/yukselme.py.
  * 8 Kas 2026: sitede görünmeyen eski iki takipçi kaldırıldı — "AL + 2–4 mum onayı" (Sinyal / onay_durumu) ve
    "Pine AL geri çekilme" (/geri). Yedek: yedek/2026-11-08_temizlik_oncesi/.

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
from dataclasses import asdict
from datetime import datetime, time, timedelta, timezone

import numpy as np
import requests
from dotenv import load_dotenv

from . import geri_al as G
from . import kagit as K
from . import yuksek as Y
from . import yukselme as YU
from . import sinyal as S
from . import bildirim as B
from .telegram import Telegram
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
# Tüm piyasa Python 'yüksek' taraması (8 Kas 2026): TV alarmları sadece izleme listelerinde tetikleniyor (2–6 Kas
# haftasında 18 alarm, 0 GİR); aynı hafta tüm hareketlilerde 22 GİR vardı (backtest/hafta_tara.py). Geniş tarama:
# hacim şartı YOK, gün değişimi > GENIS_DEGISIM, fiyat GENIS_FIYAT aralığında (backtestle aynı evren).
GENIS_DEGISIM = 5.0
GENIS_FIYAT = (0.2, 40.0)
GENIS_MAX = 500                # en çok yükselenden başlayarak (Alpaca: açılışta ~75, sonra dakikada ~10 istek)


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


def genis_tara(port: int, premarket: bool) -> list[dict]:
    """Python yüksek taraması evreni: NASDAQ/NYSE/AMEX hisseleri, gün değişimi > %GENIS_DEGISIM, fiyat 0,2–40 $."""
    alan, fiyat = ("premarket_change", "premarket_close") if premarket else ("change", "close")
    q = {"columns": KOLONLAR, "range": [0, GENIS_MAX], "markets": ["america"],
         "sort": {"sortBy": alan, "sortOrder": "desc"},
         "filter": [{"left": "type", "operation": "equal", "right": "stock"},
                    {"left": "is_primary", "operation": "equal", "right": True},
                    {"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE", "AMEX"]},
                    {"left": fiyat, "operation": "in_range", "right": list(GENIS_FIYAT)},
                    {"left": alan, "operation": "greater", "right": GENIS_DEGISIM}]}
    res = tv_cdp.request(SCAN_URL, "POST", q, text_plain=True, port=port)
    return [{"s": row["s"], **dict(zip(KOLONLAR, row["d"]))} for row in res.get("data", [])]


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


class Takipci:
    def __init__(self, port: int):
        self.port = port
        self.mumlar: dict[str, Mumlar] = {}
        self.bilgi: dict[str, dict] = {}
        self.yuksek: list[G.GeriSinyal] = []        # 'yüksek' sinyalleri — TradingView alarmlarından (tv_alarm)
        self.onceki: dict[str, float] = {}          # önceki seans kapanışı (tarayıcıdan)
        self.gun = datetime.now(NY).date()
        self.son_kapanan_dk = int(_time.time() // 60) - 1
        self.son_alpaca = 0.0
        self.gun_bas_dk = int(datetime.now(NY).replace(hour=4, minute=0, second=0, microsecond=0).timestamp() // 60)
        self.py_bitti: set[str] = set()             # Python taramasında bugün kararı verilmiş hisseler (tekrar bakılmaz)
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
            self.onceki = {k: float(v) for k, v in (d.get("onceki") or {}).items()}
            self.yuksek = [G.GeriSinyal(**{k: v for k, v in x.items() if k not in ("hedef", "stop", "hedef2")})
                           for x in d.get("yuksek", []) if x.get("tur") in ("TV", "PY")]

    def kaydet(self) -> None:
        d = {"gun": str(self.gun), "guncel": datetime.now(NY).isoformat(timespec="seconds"),
             "izlenen": len(self.mumlar),
             "yuksek_kural": {"gun_min": Y.GUN_MIN, "gun_max": Y.GUN_MAX, "mom": Y.MOM_MIN, "hacim": Y.HACIM_KAT,
                              "bbw": Y.BBW_MIN, "gunluk_max": Y.GUNLUK_MAX, "pencere_dk": G.PENCERE_DK,
                              "filtre_gun_max": Y.FILTRE_GUN_MAX, "hedef1": Y.HEDEF1, "hedef2": Y.HEDEF2,
                              "filtre_saat": _saat_yazi(Y.FILTRE_SAAT), "filtre_kirmizi_max": Y.FILTRE_KIRMIZI_MAX},
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
        for row in tara(self.port) + self._genis(premarket) + self.yukselme.tara_guncelle():
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
        izle = {x.sym for x in self.yuksek if x.durum in ("bekliyor", "al", "yarim")}
        for sym in izle - set(self.mumlar):  # yeniden başlatmada tarayıcıda olmayanlar da izlensin
            self.mumlar[sym] = Mumlar()
            yeni.append(sym)
        self._alpaca_doldur(yeni, simdi)
        if dk - 1 > self.son_kapanan_dk:
            for sym in list(self.mumlar):
                self._python_sinyal(sym, dk - 1)
            for sym in {x.sym for x in self.yuksek} & set(self.mumlar):
                self._yuksek_isle(sym, dk - 1)
            self.son_kapanan_dk = dk - 1
            self._yukselme_degerlendir(dk)
        self._guncelle_fiyatlar()
        self._telegram()
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
            if any(x.sym == a.sym for x in self.yuksek):    # günde hisse başına 1 sinyal (Python önce bulduysa)
                kayitli.add((a.sym, a.al_dk))
                continue
            pc = self.onceki.get(a.sym)
            row = self.bilgi.get(a.sym, {})
            x = G.GeriSinyal(a.sym, str(row.get("description") or ""), "TV", a.al_dk, a.fiyat,
                             (a.fiyat / pc - 1) * 100 if pc else None, seviye=a.seviye, eklendi=_time.time(),
                             gecmis=_time.time() - a.tetik > 120)
            x.alma = Y.alma_nedeni(x, None, dolum_var=False)
            self.yuksek.append(x)
            kayitli.add((a.sym, a.al_dk))      # tarayıcıda yoksa adim() 'izle' ile mum + Alpaca geçmişi ekler
            log.info("🟣 YÜKSEK (TV alarmı) %s @ %.4g · geri çekilme %.4g%s", a.sym, a.fiyat, a.seviye,
                     f" · ALMA: {x.alma}" if x.alma else "")

    def _yuksek_isle(self, sym: str, dk: int) -> None:
        """TV alarmından gelen 'yüksek' sinyali → geri çekilme alışı → seçim (ALMA / GİR) → hedef / stop."""
        m = self.mumlar[sym]
        if not m.bar:
            return
        dks, o, h, l, c, v = m.diziler(dk)
        simdi = _time.time()
        for x in self.yuksek:
            if x.sym != sym:
                continue
            if x.gun_degisim is None:
                pc = self._onceki(sym, dks, c)
                x.gun_degisim = (x.al_fiyat / pc - 1) * 100 if pc else None
            sonra = h[dks > x.al_dk]
            if len(sonra):
                x.tepe = max(x.tepe or 0.0, float(sonra.max()))
            yeni = Y.degerlendir(x, dks, o, h, l, c,       # yarı-yarı çıkış; 16:00 NY'de kapanır (Pine / kağıt gibi)
                                 seans_son_dk=Y.seans_sonu_dk(x.al_dk), simdi_dk=int(simdi // 60))
            self._secim(x, dk)
            if x.yari_dk is not None and x.yari_bildirim is None:
                x.yari_bildirim = simdi
                log.info("🟣 %s yarısı +%%%g'de satıldı, kalan içeride", sym, Y.HEDEF1)
            if yeni in ("hedef", "stop"):
                log.info("🟣 %s: %s", sym, yeni)
            elif yeni == "seans":
                log.info("🟣 %s: seans sonu (16:00) — kalan %.4g'den kapandı", sym, x.cikis_fiyat)

    def _secim(self, x: G.GeriSinyal, dk: int) -> None:
        """Sinyal sonrası seçim (yuksek.alma_nedeni). Dolumdan önce: gün/saat + şu ana kadarki kırmızı oranı (bilgi);
        dolumda kesinleşir (x.karar) ve sonra değişmez — GİR / ALMA o an verilir."""
        if x.karar:
            return
        m = self.mumlar.get(x.sym)
        bitis = x.dolum_dk if x.dolum_dk is not None else dk + 1
        if m is not None and m.bar:
            dks, o, _, _, c, _ = m.diziler(bitis - 1)
            x.kirmizi = Y.kirmizi_orani(dks, o, c, x.al_dk, bitis)
        x.alma = Y.alma_nedeni(x, x.kirmizi, dolum_var=x.dolum_dk is not None)
        if x.dolum_dk is None:
            return
        x.karar, x.al_bildirim = True, x.al_bildirim or _time.time()
        if m is not None and m.bar:
            _, _, hk, lk, ck, vk = m.diziler(bitis - 1)
            x.obv_ustu, x.kijun_ustu = Y.kalite(hk, lk, ck, vk)
        if x.alma:
            log.info("⚪ %s geri çekilme doldu @ %.4g ama ALMA: %s", x.sym, x.dolum_fiyat, x.alma)
        else:
            log.info("🟢 TAMAM — ŞARTLAR SAĞLANIYOR, GİR %s @ %.4g (kırmızı %%%.0f)", x.sym, x.dolum_fiyat,
                     (x.kirmizi or 0) * 100)

    def _genis(self, premarket: bool) -> list[dict]:
        try:
            return genis_tara(self.port, premarket)
        except tv_cdp.TVError as e:
            log.warning("geniş tarama: %s", e)
            return []

    def _python_sinyal(self, sym: str, dk: int) -> None:
        """Tüm piyasa: Python 'yüksek' kuralı (sistem/yuksek.py = pine/yuksek.pine). Hisse başına günde 1 sinyal.
        Siteye SADECE GİR olabilecekler eklenir: taze (≤ PENCERE_DK − 1 dk) ve sinyalde gün/saat şartı uygun."""
        if sym in self.py_bitti or any(x.sym == sym for x in self.yuksek):
            return
        m = self.mumlar.get(sym)
        if m is None or not m.bar:
            return
        dks, o, h, l, c, v = m.diziler(dk)
        pc = self._onceki(sym, dks, c)
        if not pc or not len(c) or (c[-1] / pc - 1) * 100 < Y.GUN_MIN:
            return
        idx = Y.yuksek_sinyalleri(dks, o, h, l, c, v, S.gostergeler(o, h, l, c, v), pc, self.gun_bas_dk)
        if not idx:
            return
        i = idx[0]
        self.py_bitti.add(sym)                        # günün ilk sinyali bulundu — karar bir kez verilir
        row = self.bilgi.get(sym, {})
        x = G.GeriSinyal(sym, str(row.get("description") or ""), "PY", int(dks[i]), float(c[i]),
                         (c[i] / pc - 1) * 100, seviye=round(float(c[i]) * (1 - G.GERI / 100), 6),
                         eklendi=_time.time())
        if dk - x.al_dk > G.PENCERE_DK - 1:
            return                                    # geç görüldü: geri çekilme penceresi geçmiş
        if Y.alma_nedeni(x, None, dolum_var=False):
            return                                    # sabah / gün > %20 → GİR olamaz, siteye eklenmez
        self.yuksek.append(x)
        log.info("🔵 YÜKSEK (Python taraması) %s @ %.4g · gün %+.1f%% · geri çekilme %.4g", sym, x.al_fiyat,
                 x.gun_degisim, x.seviye)

    def _onceki(self, sym: str, dks, c) -> float | None:
        return self.onceki.get(sym) or onceki_kapanis(dks, c, self.gun_bas_dk)

    def _guncelle_fiyatlar(self) -> None:
        dk = int(_time.time() // 60)
        for x in self.yuksek:
            m = self.mumlar.get(x.sym)
            if m is None or not m.bar:
                continue
            x.son_fiyat = m.bar[max(m.bar)][3]
            if max(m.bar) in m.canli_dk and G.canli_dolum(x, x.son_fiyat, dk):
                self._secim(x, dk)


def _saat_yazi(s: tuple[int, int] | None) -> str | None:
    return f"{s[0]}:{s[1]:02d}" if s else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aralik", type=int, default=10, help="tarama aralığı (sn)")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--web-port", type=int, default=8765)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler(FILTER_DIR / "devam_takip.log", encoding="utf-8")])
    load_dotenv(FILTER_DIR.parent / ".env")
    from .devam_web import baslat
    threading.Thread(target=baslat, args=(a.web_port,), daemon=True).start()
    log.info("web: http://127.0.0.1:%d · yüksek alarmları + sinyal sonrası seçim (gün ≤ %s · sinyal ≥ %s · kırmızı ≤ %s)",
             a.web_port, Y.FILTRE_GUN_MAX, _saat_yazi(Y.FILTRE_SAAT), Y.FILTRE_KIRMIZI_MAX)
    t = Takipci(a.port)
    close = todays_close(datetime.now(NY).date())
    from .video_ekran import EkranAlici
    ekran = EkranAlici(a.web_port)                 # günün videosu için site ekran görüntüleri
    while True:
        now = datetime.now(NY)
        ekran.kontrol(now)
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
