"""sistem/yukselme.py — yükselme adayları: premarket tepesi kırılımı, hacim, karar sırası, haber penceresi."""
from datetime import datetime

import numpy as np

from sistem import yukselme as YU

ACILIS = 100   # sahte dakikalar: 100 = 09:30


def _seri(kapanislar, hacimler, bas=95):
    n = len(kapanislar)
    dks = np.arange(bas, bas + n)
    c = np.array(kapanislar, float)
    return dks, c + 0.01, c, np.array(hacimler, float)


def test_kirip_ustunde_tutunan_ve_hacimli():
    dks, h, c, v = _seri([1.0] * 5 + [1.0, 1.02, 1.12, 1.15, 1.14], [100] * 7 + [500, 200, 150])
    durum, dk, hk = YU.kirilim_durumu(dks, h, c, v, pm_tepe=1.10, acilis_dk=ACILIS, son_dk=104)
    assert durum == "tutunuyor" and dk == 102 and hk == 5.0


def test_kirip_geri_dusen_gap_dolma_riski():
    dks, h, c, v = _seri([1.0] * 5 + [1.05, 1.12, 1.08, 1.05], [100] * 9)
    assert YU.kirilim_durumu(dks, h, c, v, 1.10, ACILIS, 103)[0] == "geri_dondu"


def test_kiramayip_altinda_kalan():
    dks, h, c, v = _seri([1.0] * 5 + [1.05, 1.04, 1.02], [100] * 8)
    assert YU.kirilim_durumu(dks, h, c, v, 1.10, ACILIS, 102)[0] == "kiramadi"


def test_premarket_mumlari_kirilim_sayilmaz():
    dks, h, c, v = _seri([1.2] * 5, [100] * 5)                # hepsi 09:30'dan önce
    assert YU.kirilim_durumu(dks, h, c, v, 1.10, ACILIS, 99) == ("bekliyor", None, None)


def _aday(**kw):
    a = YU.Aday("NASDAQ:ABC", haber_sayisi=1, asama="seans", kirilim="tutunuyor", kirilim_hacim_kat=3.0)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def test_haber_yoksa_her_durumda_gec():
    assert YU.karar_ver(_aday(haber_sayisi=0))[0] == "GEÇ"


def test_ilk_bes_dakikada_bekle():
    assert YU.karar_ver(_aday(asama="ilk5"))[0] == "BEKLE"


def test_tum_sartlar_saglaninca_devam_adayi():
    assert YU.karar_ver(_aday())[0] == "DEVAM ADAYI"


def test_hacim_dusukse_devam_adayi_degil():
    assert YU.karar_ver(_aday(kirilim_hacim_kat=1.3))[0] == "İZLE"


def test_geri_donen_zayif():
    assert YU.karar_ver(_aday(kirilim="geri_dondu"))[0] == "ZAYIF"


def test_haber_penceresi_onceki_islem_gunu_kapanisi():
    pazartesi = datetime(2026, 11, 2, 7, 0, tzinfo=YU.NY)
    assert YU.onceki_kapanis_ani(pazartesi) == datetime(2026, 10, 30, 16, 0, tzinfo=YU.NY)   # cuma 16:00
    sali_aksam = datetime(2026, 11, 3, 17, 0, tzinfo=YU.NY)
    assert YU.onceki_kapanis_ani(sali_aksam) == datetime(2026, 11, 3, 16, 0, tzinfo=YU.NY)


def test_asama_ny_saatiyle():
    assert YU.asama(datetime(2026, 11, 4, 9, 29, tzinfo=YU.NY)) == "premarket"
    assert YU.asama(datetime(2026, 11, 4, 9, 33, tzinfo=YU.NY)) == "ilk5"
    assert YU.asama(datetime(2026, 11, 4, 9, 35, tzinfo=YU.NY)) == "seans"


def test_yuksek_geri_cekilme_doldu_ve_alma_filtresi():
    a = YU.Aday("NASDAQ:ABC")
    dks = np.arange(0, 10)
    c = np.array([1.0] * 10); l = c.copy(); l[4] = 0.94          # sinyal mumu 2, 4. dk'da −%6
    YU.yuksek_durumu(a, 2, dks, l, c, onceki=0.8, simdi_dk=10)    # gün +%25
    assert a.yuksek_durum == "doldu" and a.yuksek_dolum_dk == 4 and a.yuksek_alma is True


def test_yuksek_otuz_dakikada_inmezse_olmadi():
    a = YU.Aday("NASDAQ:ABC")
    dks = np.arange(0, 40); c = np.ones(40); l = c.copy()
    YU.yuksek_durumu(a, 2, dks, l, c, onceki=0.9, simdi_dk=40)
    assert a.yuksek_durum == "olmadi" and a.yuksek_alma is False


def test_ozet_json_yazilabilir():
    import json
    ad = YU.Adaylar()
    a = YU.Aday("NASDAQ:ABC")
    YU.yuksek_durumu(a, 2, np.arange(0, 10), np.ones(10), np.ones(10), onceki=0.9, simdi_dk=5)
    ad.adaylar[a.sym] = a
    json.dumps(ad.ozet())


def test_tv_alarmi_hesaplananin_yerine_gecer():
    from sistem import geri_al as G
    a = YU.Aday("NASDAQ:WORX")
    x = G.GeriSinyal("NASDAQ:WORX", "", "TV", 100, 1.66, 16.1, seviye=1.58, durum="bekliyor")
    YU.tv_alarmindan(a, x)
    assert (a.yuksek_kaynak, a.yuksek_dk, a.yuksek_seviye, a.yuksek_durum, a.yuksek_alma) == ("TV", 100, 1.58, "bekliyor", False)
