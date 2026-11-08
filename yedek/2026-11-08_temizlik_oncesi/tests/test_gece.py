"""sistem/gece.py — seans sonrası (gece) yükseliş taraması: saat penceresi, basamaklar, bir kez bildirim."""
from datetime import datetime, time

from sistem import gece as GC
from sistem.bildirim import Bildirici

NY = GC.NY
CIIT = {"s": "NASDAQ:CIIT", "name": "CIIT", "description": "Tianci International", "close": 1.55,
        "postmarket_close": 2.48, "postmarket_change": 60.0, "postmarket_volume": 250_000,
        "postmarket_high": 2.58, "float_shares_outstanding_current": 729_324}


def _ny(h, m):
    return datetime(2026, 11, 5, h, m, tzinfo=NY)


def test_gece_penceresi_kapanistan_20ye_kadar():
    assert not GC.gece_mi(_ny(15, 59), time(16, 0))
    assert GC.gece_mi(_ny(16, 0), time(16, 0))
    assert GC.gece_mi(_ny(19, 59), time(16, 0))
    assert not GC.gece_mi(_ny(20, 0), time(16, 0))


def test_yarim_gun_ve_tatil():
    assert GC.gece_mi(_ny(13, 30), time(13, 0))      # yarım gün: 13:00'te kapanır
    assert not GC.gece_mi(_ny(17, 0), None)           # işlem günü değil


def test_basamak():
    assert GC.gecilen_basamak(None) is None
    assert GC.gecilen_basamak(15.0) is None
    assert GC.gecilen_basamak(45.0) == 30.0
    assert GC.gecilen_basamak(60.6) == 60.0
    assert GC.gecilen_basamak(140.0) == 100.0


def test_satirdan_ve_mesaj():
    h = GC.satirdan(CIIT, 1.0)
    h.haberler = [{"baslik": "Tianci Reports Financial Results <FY2026>"}]
    m = GC.mesaj(h, "🌙 GECE YÜKSELİŞİ")
    assert "CIIT" in m and "1.550" in m and "2.480" in m and "+60.0%" in m
    assert "float 0.7M" in m and "&lt;FY2026&gt;" in m          # html kaçışı


def test_haber_yoksa_belirtir():
    assert "Haber yok" in GC.mesaj(GC.satirdan(CIIT, 1.0), "x")


class _Sahte(GC.GeceTakip):
    def __init__(self, satirlar, bildirici):
        super().__init__(0, bildirici)
        self.satirlar = satirlar

    def tara(self):
        return self.satirlar

    def _haber(self, sym, simdi):
        return []

    def kaydet(self):
        pass

    def liste_guncelle(self):
        self.listeler = getattr(self, "listeler", []) + [self.liste_sembolleri()]


def test_liste_dusenleri_tutar_ve_siralar(tmp_path):
    t = _Sahte([CIIT, {**CIIT, "s": "NASDAQ:ABC", "postmarket_change": 90.0}],
               Bildirici(lambda s: None, tmp_path / "g.json", "2026-11-05"))
    t.adim(_ny(17, 5))
    assert t.listeler[-1] == ["NASDAQ:ABC", "NASDAQ:CIIT"]
    t.satirlar = [CIIT]                                   # ABC tarayıcıdan düştü → listede kalır
    t.adim(_ny(17, 6))
    assert t.listeler[-1] == ["NASDAQ:ABC", "NASDAQ:CIIT"]


def test_her_hisse_bir_kez_ve_basamakta_tekrar(tmp_path):
    giden = []
    t = _Sahte([{**CIIT, "postmarket_change": 15.0}], Bildirici(giden.append, tmp_path / "g.json", "2026-11-05"))
    t.adim(_ny(17, 5))
    t.adim(_ny(17, 6))
    assert len(giden) == 1 and "GECE YÜKSELİŞİ" in giden[0]
    t.satirlar = [{**CIIT, "postmarket_change": 62.0}]
    t.adim(_ny(17, 10))                                   # +%30'u atlayıp +%60'ı geçti → tek mesaj
    assert len(giden) == 2 and "+%60 ÜSTÜ" in giden[-1]
    t.adim(_ny(17, 11))
    assert len(giden) == 2
    t.satirlar = [{**CIIT, "postmarket_change": 105.0}]
    t.adim(_ny(17, 20))
    assert len(giden) == 3 and "+%100 ÜSTÜ" in giden[-1]


def test_ilk_gorulme_korunur(tmp_path):
    t = _Sahte([CIIT], Bildirici(lambda s: None, tmp_path / "g.json", "2026-11-05"))
    t.adim(_ny(17, 5))
    ilk = t.hisseler["NASDAQ:CIIT"].ilk_gorulme
    t.adim(_ny(17, 30))
    assert t.hisseler["NASDAQ:CIIT"].ilk_gorulme == ilk
