import numpy as np

from sistem import sinyal as S
from sistem import yuksek as Y


def _seri(son_hacim=5000.0, son_fiyat=2.4, n=80):
    c = np.full(n, 2.0) + np.sin(np.arange(n)) * 0.01
    c[-1] = son_fiyat                                   # son mumda BB kesişi + sert ivme
    v = np.full(n, 1000.0)
    v[-1] = son_hacim
    o = np.concatenate([[c[0]], c[:-1]])
    return np.arange(n), o, np.maximum(o, c) + 0.001, np.minimum(o, c) - 0.001, c, v


def _calis(*seri, onceki=2.0, gun_bas=0):
    dks, o, h, l, c, v = seri
    g = S.gostergeler(o, h, l, c, v)
    return Y.yuksek_sinyalleri(dks, o, h, l, c, v, g, onceki, gun_bas)


def test_tum_kosullar_saglaninca_sinyal():
    assert _calis(*_seri()) == [79]


def test_hacim_dusukse_sinyal_yok():
    assert _calis(*_seri(son_hacim=1500.0)) == []


def test_gun_degisimi_aralik_disindaysa_sinyal_yok():
    assert _calis(*_seri(), onceki=2.35) == []          # gün +%2
    assert _calis(*_seri(), onceki=1.5) == []           # gün +%60


def test_gun_baslangicindan_once_sayilmaz():
    assert _calis(*_seri(), gun_bas=80) == []


def test_gunde_en_cok_bir():
    assert len(_calis(*_seri())) <= Y.GUNLUK_MAX


def _gs(dolum=1.0):
    from sistem import geri_al as G
    return G.GeriSinyal("X", "", "TV", 0, 1.05, 10.0, seviye=dolum, durum="al", dolum_dk=1, dolum_fiyat=dolum)


def _mumlar(yuksekler, kapanislar):
    n = len(yuksekler)
    dks = np.arange(1, n + 1)
    h, c = np.array(yuksekler, float), np.array(kapanislar, float)
    return dks, c, h, np.minimum(c, h), c


def test_yari_yari_once_yarim_sonra_hedef():
    x = _gs()
    Y.degerlendir(x, *_mumlar([1.0, 1.06, 1.10], [1.0, 1.04, 1.08]))
    assert x.durum == "yarim" and x.yari_dk == 2
    Y.degerlendir(x, *_mumlar([1.0, 1.06, 1.10, 1.21], [1.0, 1.04, 1.08, 1.2]))
    assert x.durum == "hedef" and x.sonuc_dk == 4


def test_yari_satildiktan_sonra_stop():
    x = _gs()
    Y.degerlendir(x, *_mumlar([1.0, 1.06, 0.95], [1.0, 1.04, 0.90]))
    assert x.durum == "stop" and x.yari_dk == 2


def test_hedefe_gelmeden_stop():
    x = _gs()
    Y.degerlendir(x, *_mumlar([1.0, 1.02, 0.95], [1.0, 1.0, 0.91]))
    assert x.durum == "stop" and x.yari_dk is None


def test_seans_sonrasi_mumlar_sayilmaz_acik_pozisyon_seansta_kapanir():
    # WORX 3 Kas: dolum seansta, hedefler sadece 16:00'dan sonra geldi → hedef değil, seans sonu kapanışı
    x = _gs()
    dks, o, h, l, c = _mumlar([1.0, 1.02, 1.03, 1.30], [1.0, 1.01, 1.02, 1.25])
    yeni = Y.degerlendir(x, dks, o, h, l, c, seans_son_dk=4)            # 4. mum = seans sonrası
    assert yeni == "seans" and x.sonuc_dk == 3 and x.cikis_fiyat == 1.02 and x.yari_dk is None


def test_seans_bitmeden_pozisyon_acik_kalir():
    x = _gs()
    assert Y.degerlendir(x, *_mumlar([1.0, 1.02], [1.0, 1.01]), seans_son_dk=10) is None
    assert x.durum == "al"


def test_yarisi_satildiktan_sonra_seans_sonu():
    x = _gs()
    Y.degerlendir(x, *_mumlar([1.0, 1.06, 1.08], [1.0, 1.04, 1.07]), seans_son_dk=4, simdi_dk=5)
    assert x.durum == "seans" and x.yari_dk == 2 and x.cikis_fiyat == 1.07


def test_seansta_dolmayan_emir_seans_bitince_yok():
    from sistem import geri_al as G
    x = G.GeriSinyal("X", "", "TV", 0, 1.0, 10.0, seviye=0.95)
    dks, o, h, l, c = _mumlar([1.0, 1.0, 0.9], [1.0, 1.0, 0.9])            # 0.9 dip seans sonrasında (dk 3)
    assert Y.degerlendir(x, dks, o, h, l, c, seans_son_dk=3) == "yok" and x.dolum_dk is None


def test_seans_sonu_dakikasi_ny_saatiyle():
    from datetime import datetime
    al = int(datetime(2026, 11, 3, 15, 49, tzinfo=Y.NY).timestamp() // 60)
    assert Y.seans_sonu_dk(al) == int(datetime(2026, 11, 3, 16, 0, tzinfo=Y.NY).timestamp() // 60)
