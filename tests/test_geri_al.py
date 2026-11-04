import numpy as np

from sistem import geri_al as G
from sistem import sinyal as S


def _gs(al_fiyat=2.0, al_dk=100):
    return G.GeriSinyal("NASDAQ:X", "X", "BB", al_dk, al_fiyat, 20.0, seviye=al_fiyat * 0.95)


def _calis(gs, c, l=None, h=None, o=None, bas=101):
    c = np.asarray(c, float)
    dks = np.arange(bas, bas + len(c))
    l = c if l is None else np.asarray(l, float)
    h = c if h is None else np.asarray(h, float)
    o = c if o is None else np.asarray(o, float)
    return G.degerlendir(gs, dks, o, h, l, c)


def test_seviyeye_inince_al_ve_alis_fiyati_seviye():
    gs = _gs()
    assert _calis(gs, [1.98, 1.93], l=[1.97, 1.89]) == "al"
    assert gs.dolum_fiyat == 1.9 and gs.dolum_dk == 102
    assert abs(gs.hedef - 1.995) < 1e-9 and abs(gs.stop - 1.748) < 1e-9


def test_bosluklu_acilista_alis_acilis_fiyati():
    gs = _gs()
    _calis(gs, [1.85], l=[1.84], o=[1.86])
    assert gs.dolum_fiyat == 1.86


def test_al_sonrasi_hedef():
    gs = _gs()
    assert _calis(gs, [1.92, 1.96, 2.0], l=[1.89, 1.95, 1.99], h=[1.93, 1.97, 2.0]) == "hedef"
    assert gs.sonuc_dk == 103


def test_al_sonrasi_stop():
    gs = _gs()
    assert _calis(gs, [1.92, 1.7], l=[1.89, 1.69]) == "stop"


def test_pencerede_geri_cekilme_yoksa_yok():
    gs = _gs()
    assert _calis(gs, [2.05] * (G.PENCERE_DK + 1)) == "yok"


def test_pencere_bitmeden_bekliyor_ve_degisim_yoksa_none():
    gs = _gs()
    assert _calis(gs, [2.05, 2.02]) is None and gs.durum == "bekliyor"


def test_canli_fiyat_seviyeye_inince_hemen_al():
    gs = _gs()
    assert G.canli_dolum(gs, 1.9, 110) and gs.durum == "al" and gs.dolum_fiyat == 1.9
    assert not G.canli_dolum(_gs(), 1.9, 100 + G.PENCERE_DK + 1)


def test_pine_al_pozisyondayken_yeni_al_saymaz():
    n = 120
    c = np.full(n, 2.0) + np.sin(np.arange(n)) * 0.001
    c[70:] = 2.0 + np.arange(n - 70) * 0.05            # sürekli yükseliş: pozisyon açık kalır (SÜRE 20'ye kadar)
    g = S.gostergeler(c, c + 0.002, c - 0.002, c, np.ones(n))
    als = G.pine_al_lari(np.arange(n), c, g, gun_bas_dk=60)
    assert als and all(b[0] - a[0] > G.SURE_MUM for a, b in zip(als, als[1:]))
