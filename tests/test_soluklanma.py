import numpy as np

from backtest.soluklanma import cikis, kirilimlar, kutu_ozellikleri


def _gogo_benzeri():
    # 30 mum yatay 2,20 → 10 mumda 2,80'e direk → 10 mum 2,78–2,85 kutu → kırılım 2,95 (yüksek hacim)
    c = np.r_[np.full(30, 2.20), np.linspace(2.26, 2.80, 10), np.tile([2.80, 2.83], 5), [2.95]]
    h, l = c + 0.01, c - 0.01
    v = np.r_[np.full(50, 1000.0), [5000.0]]
    return c, h, l, v


def test_gogo_benzeri_kalipta_kirilim_mumu_bulunur():
    c, h, l, v = _gogo_benzeri()
    oz = kutu_ozellikleri(h, l, v, n=10)
    idx = kirilimlar(c, v, vw=np.full(len(c), 2.5), gun=np.full(len(c), 30.0), oz=oz, r_max=5, i_min=8, hk=2)
    assert list(idx) == [len(c) - 1]


def test_hacimsiz_kirilim_sayilmaz():
    c, h, l, v = _gogo_benzeri()
    v[-1] = 1000.0
    oz = kutu_ozellikleri(h, l, v, n=10)
    assert len(kirilimlar(c, v, np.full(len(c), 2.5), np.full(len(c), 30.0), oz, 5, 8, 2)) == 0


def test_direk_yoksa_kirilim_sayilmaz():
    c, h, l, v = _gogo_benzeri()
    c[:40], h[:40], l[:40] = 2.79, 2.80, 2.78
    oz = kutu_ozellikleri(h, l, v, n=10)
    assert len(kirilimlar(c, v, np.full(len(c), 2.5), np.full(len(c), 30.0), oz, 5, 8, 2)) == 0


def test_cikis_kutu_dibi_stopu():
    o = np.array([3.0, 3.0, 3.0])
    c = np.array([2.95, 2.70, 3.5])
    r, k = cikis(o, c, c, 0, 3, alt=2.78, tur="kutu_10")
    assert k == 1 and round(r, 4) == -10.0


def test_cikis_hedef_5():
    o = np.array([2.0, 2.0])
    h = np.array([2.02, 2.20])
    c = np.array([2.01, 2.15])
    r, k = cikis(o, h, c, 0, 2, alt=1.9, tur="5_8")
    assert k == 1 and round(r, 6) == 5.0
