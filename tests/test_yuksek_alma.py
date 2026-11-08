"""yüksek sinyali SONRASI seçim (8 Kas 2026): gün ≤ %20 · sinyal ≥ 11:30 NY · sakin geri çekilme (kırmızı ≤ %43)."""
from datetime import datetime

import numpy as np

from sistem import bildirim as B
from sistem import geri_al as G
from sistem import yuksek as Y


def _dk(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=Y.NY).timestamp() // 60)


def _sinyal(saat="2026-11-06T12:00", gun=10.0, **kw) -> G.GeriSinyal:
    return G.GeriSinyal("NASDAQ:X", "X", "TV", _dk(saat), 2.0, gun, seviye=1.9, **kw)


def test_kirmizi_orani_sinyal_ile_dolum_arasindaki_mumlar():
    al = _dk("2026-11-06T12:00")
    dks = np.arange(al, al + 6)
    o = np.array([2.0, 2.0, 1.98, 1.96, 1.95, 1.93])
    c = np.array([2.0, 1.99, 1.99, 1.95, 1.96, 1.90])     # arası (1–4): kırmızı, yeşil, kırmızı, yeşil
    assert Y.kirmizi_orani(dks, o, c, al, al + 5) == 0.5


def test_kirmizi_orani_arada_mum_yoksa_none():
    al = _dk("2026-11-06T12:00")
    assert Y.kirmizi_orani(np.array([al, al + 1]), np.ones(2), np.ones(2), al, al + 1) is None


def test_ogleden_sonra_sakin_geri_cekilme_alinir():
    assert Y.alma_nedeni(_sinyal(), kirmizi=0.25, dolum_var=True) is None


def test_sabah_sinyali_alinmaz():
    neden = Y.alma_nedeni(_sinyal("2026-11-06T10:15"), kirmizi=0.0, dolum_var=True)
    assert neden is not None and "11:30" in neden


def test_sert_geri_cekilme_alinmaz():
    neden = Y.alma_nedeni(_sinyal(), kirmizi=0.6, dolum_var=True)
    assert neden is not None and "kırmızı" in neden


def test_tek_mumda_dolum_alinmaz():
    assert "tek mumda" in Y.alma_nedeni(_sinyal(), kirmizi=None, dolum_var=True)


def test_gun_yuzde_20_ustu_alinmaz():
    assert "gün" in Y.alma_nedeni(_sinyal(gun=25.0), kirmizi=0.1, dolum_var=True)


def test_dolum_yokken_kirmizi_karar_vermez():
    assert Y.alma_nedeni(_sinyal(), kirmizi=0.9, dolum_var=False) is None


def test_telegram_gir_sadece_karar_verilmis_ve_alinabilir_sinyalde():
    x = _sinyal(durum="al", dolum_dk=1, dolum_fiyat=1.9)
    assert not B.girilecek(x)                     # karar henüz verilmedi
    x.karar = True
    assert B.girilecek(x)
    x.alma = "sert geri çekilme"
    assert not B.girilecek(x)


def _takipci_mumlarla(al: int, renkler: str):
    """Takipci'yi ağ/TV olmadan kurar: sinyal mumu + verilen renklerde ara mumlar (k=kırmızı, y=yeşil)."""
    from sistem.devam_takip import Mumlar, Takipci
    t = object.__new__(Takipci)
    m = Mumlar()
    m.alpaca_ekle(al, 2.0, 2.0, 2.0, 2.0, 100)
    for i, r in enumerate(renkler, 1):
        o, c = (2.0, 1.98) if r == "k" else (1.98, 2.0)
        m.alpaca_ekle(al + i, o, 2.0, 1.9, c, 100)
    t.mumlar = {"NASDAQ:X": m}
    return t


def test_takipci_secimi_dolumda_kesinlesir_ve_degismez():
    x = _sinyal()
    t = _takipci_mumlarla(x.al_dk, "kyy")             # 3 ara mumun 1'i kırmızı → %33 → GİR
    t._secim(x, x.al_dk + 3)
    assert not x.karar and x.alma is None             # dolum yok: karar yok
    x.dolum_dk, x.dolum_fiyat, x.durum = x.al_dk + 4, 1.9, "al"
    t._secim(x, x.al_dk + 4)
    assert x.karar and x.alma is None and abs(x.kirmizi - 1 / 3) < 1e-9 and B.girilecek(x)
    t.mumlar["NASDAQ:X"].alpaca_ekle(x.al_dk + 2, 2.0, 2.0, 1.9, 1.95, 100)   # sonradan gelen mum karar değiştirmez
    t._secim(x, x.al_dk + 6)
    assert x.alma is None


def test_takipci_sert_geri_cekilmede_alma_der():
    x = _sinyal()
    t = _takipci_mumlarla(x.al_dk, "kky")             # %67 kırmızı
    x.dolum_dk, x.dolum_fiyat, x.durum = x.al_dk + 4, 1.9, "al"
    t._secim(x, x.al_dk + 4)
    assert x.karar and "kırmızı" in x.alma and not B.girilecek(x)
