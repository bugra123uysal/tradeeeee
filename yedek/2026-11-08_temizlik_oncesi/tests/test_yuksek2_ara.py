import numpy as np

from backtest.yuksek2_ara import giris_bul, iz_stop


def test_iz_stop_kosuyu_tasir_ve_tepeden_dususte_satar():
    # giriş 100 → 150'ye koşu, sonra 120'ye düşüş: %15 iz stop 150·0,85 = 127,5 altında (120) satar
    c = np.array([101, 110, 130, 150, 140, 120, 110], float)
    h = c + 1
    r, tepe = iz_stop(c, h, 100.0, stop=12, iz=15)
    assert round(r, 6) == 20.0
    assert round(tepe, 6) == 51.0


def test_iz_stop_ilk_stopta_satar():
    c = np.array([99, 95, 87, 120], float)
    r, _ = iz_stop(c, c, 100.0, stop=12, iz=20)
    assert round(r, 6) == -13.0


def test_iz_stop_hic_tetiklenmezse_son_kapanista_satar():
    c = np.array([101, 103, 105], float)
    r, _ = iz_stop(c, c, 100.0, stop=10, iz=15)
    assert round(r, 6) == 5.0


def test_kirilim_girisi_sinyal_tepesini_gecince_dolar():
    dks = np.arange(10)
    o = np.array([1.0, 1.0, 1.02, 1.05, 1.1, 1.1, 1.1, 1.1, 1.1, 1.1])
    h = np.array([1.0, 1.03, 1.02, 1.06, 1.1, 1.1, 1.1, 1.1, 1.1, 1.1])
    k0, giris = giris_bul(1, dks, o, h, son=10, tur="kir")
    assert k0 == 3
    assert giris == 1.05


def test_kirilim_olmazsa_giris_yok():
    dks = np.arange(5)
    o = h = np.array([1.0, 1.05, 1.0, 1.0, 1.0])
    assert giris_bul(1, dks, o, h, son=5, tur="kir") is None
