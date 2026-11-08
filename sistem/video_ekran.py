"""Günün videosu için sitenin ekran görüntüleri + o anki veri (6 Kas 2026).

Takipçi (devam_takip) döngüsünde `EkranAlici.kontrol(simdi_ny)` çağrılır; saati gelen sayfa
bir kez çekilir → filtreler/video_ekran/<gün>/site_<ad>.png (1080x1920, telefon görünümü) + <ad>.json (veri kopyası).
Gece video akışı (trade-sirketi, TR 01:30 ≈ NY 18:30) bunları sahne olarak kullanır — çekimler ondan önce biter.
Çekim Edge'in başsız modu ile yapılır (kurulum gerektirmez); TradingView'e dokunulmaz.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

log = logging.getLogger("video_ekran")
ROOT = Path(__file__).resolve().parent.parent
FILTER_DIR = ROOT / "filtreler"
DIZIN = FILTER_DIR / "video_ekran"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
PENCERE_SAAT = 3            # saati geçtikten sonra bu kadar saat içinde hâlâ çekilebilir


@dataclass(frozen=True)
class Cekim:
    ad: str
    saat: time              # NY
    yol: str                # site yolu
    veri: str               # kopyalanacak veri dosyası (filtreler/ altında)


CEKIMLER = (
    Cekim("yukselme", time(10, 0), "/yukselme", "devam_sinyaller.json"),   # ilk 5 dk + kırılımlar görünür olsun
    Cekim("yuksek", time(15, 50), "/", "devam_sinyaller.json"),             # günün sinyalleri, kapanıştan hemen önce
)


def ekran_al(url: str, hedef: Path, edge: Path = EDGE, zaman_asimi: int = 60) -> bool:
    """Sayfayı 540x960 CSS piksel × 2 ölçekle (= 1080x1920 PNG) çeker; sayfanın veri çekmesi için 8 sn sanal süre."""
    hedef.parent.mkdir(parents=True, exist_ok=True)
    profil = DIZIN / "_edge_profil"
    cmd = [str(edge), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
           "--window-size=540,960", "--virtual-time-budget=8000", f"--user-data-dir={profil}",
           f"--screenshot={hedef}", url]
    try:
        subprocess.run(cmd, capture_output=True, timeout=zaman_asimi, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("ekran alınamadı %s: %s", url, e)
        return False
    return hedef.exists() and hedef.stat().st_size > 20_000      # boş/siyah sayfa ~10 KB


ATILAN = ("sinyaller", "geri", "onceki")      # devam_sinyaller.json'un büyük kısımları (MB'larca) — videoda kullanılmıyor


def ozet_veri(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in ATILAN}


class EkranAlici:
    def __init__(self, web_port: int = 8765, cekimler: tuple[Cekim, ...] = CEKIMLER, dizin: Path = DIZIN):
        self.port, self.cekimler, self.dizin = web_port, cekimler, dizin
        self._denendi: set[tuple[str, str]] = set()

    def bekleyenler(self, simdi_ny: datetime) -> list[Cekim]:
        gun, t = str(simdi_ny.date()), simdi_ny.time()
        out = []
        for c in self.cekimler:
            bitis = time(min(23, c.saat.hour + PENCERE_SAAT), c.saat.minute)
            if (gun, c.ad) in self._denendi or not (c.saat <= t < bitis):
                continue
            if (self.dizin / gun / f"site_{c.ad}.png").exists():
                continue
            out.append(c)
        return out

    def cek(self, c: Cekim, gun: str) -> bool:
        hedef = self.dizin / gun / f"site_{c.ad}.png"
        tamam = ekran_al(f"http://127.0.0.1:{self.port}{c.yol}", hedef)
        kaynak = FILTER_DIR / c.veri
        if tamam and kaynak.exists():
            try:
                veri = ozet_veri(json.loads(kaynak.read_text(encoding="utf-8")))
                (self.dizin / gun / f"{c.ad}.json").write_text(json.dumps(veri, ensure_ascii=False), encoding="utf-8")
            except (OSError, ValueError) as e:
                log.warning("video verisi yazılamadı %s: %s", c.ad, e)
        log.info("video ekranı %s: %s", c.ad, "çekildi" if tamam else "ÇEKİLEMEDİ")
        return tamam

    def kontrol(self, simdi_ny: datetime) -> None:
        """Döngüden çağrılır; çekimi arka planda yapar (tarama döngüsünü bekletmez). Her çekim günde bir kez denenir."""
        gun = str(simdi_ny.date())
        for c in self.bekleyenler(simdi_ny):
            self._denendi.add((gun, c.ad))
            threading.Thread(target=self.cek, args=(c, gun), daemon=True).start()
