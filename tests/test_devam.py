import numpy as np

from sistem import sinyal as S
from sistem.devam_takip import Mumlar, Sinyal, gunun_al_lari, onay_durumu, onceki_kapanis


# ── canlı mum kurucu ──
def test_canli_fiyatlardan_ohlc_kurulur():
    m = Mumlar()
    for p, v in [(2.0, 100), (2.3, 150), (1.9, 180), (2.1, 200)]:
        m.fiyat_ekle(10, p, v)
    assert m.bar[10] == [2.0, 2.3, 1.9, 2.1, 100.0]


def test_alpaca_mumu_canli_tahmini_ezer_ve_korunur():
    m = Mumlar()
    m.fiyat_ekle(10, 2.0, 100)
    m.alpaca_ekle(10, 1.9, 2.5, 1.8, 2.2, 5000)
    m.fiyat_ekle(10, 3.0, 200)
    assert m.bar[10] == [1.9, 2.5, 1.8, 2.2, 5000]


def test_diziler_sadece_kapanmis_mumlari_verir():
    m = Mumlar()
    m.fiyat_ekle(10, 2.0, None)
    m.fiyat_ekle(11, 2.1, None)
    dks, *_ , c, v = m.diziler(10)
    assert list(dks) == [10] and list(c) == [2.0]


# ── AL ──
def _seri(n=80):
    c = np.full(n, 2.0) + np.sin(np.arange(n)) * 0.001
    return np.arange(1000, 1000 + n), c.copy(), c + 0.002, c - 0.002, c.copy(), np.full(n, 1000.0)


def test_son_mumda_bb_kesisi_al_verir():
    dks, o, h, l, c, v = _seri()
    c[-1] = h[-1] = 2.2
    assert gunun_al_lari(dks, o, h, l, c, v, gun_bas_dk=0)[-1] == ("BB", int(dks[-1]), 2.2, 1)


def test_gunun_ilk_al_indan_itibaren_hepsi_sirali_gelir():
    dks, o, h, l, c, v = _seri(120)
    c[70] = h[70] = 2.2                      # sabah AL'ı
    c[110] = h[110] = 2.6                    # sonraki AL
    als = gunun_al_lari(dks, o, h, l, c, v, gun_bas_dk=int(dks[60]))
    assert [x[1] for x in als][:1] == [int(dks[70])] and [x[3] for x in als] == list(range(1, len(als) + 1))


def test_gun_baslangicindan_onceki_al_sayilmaz():
    dks, o, h, l, c, v = _seri()
    c[-1] = h[-1] = 2.2
    assert gunun_al_lari(dks, o, h, l, c, v, gun_bas_dk=int(dks[-1]) + 1) == []


def test_az_mumda_al_aranmaz():
    dks, o, h, l, c, v = _seri(30)
    c[-1] = 2.2
    assert gunun_al_lari(dks, o, h, l, c, v, gun_bas_dk=0) == []


def test_onceki_kapanis_gece_seansini_atlar():
    from datetime import datetime
    from sistem.liste_yenile import NY
    dk = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=NY).timestamp() // 60)
    dks = np.array([dk("2026-10-30T15:59"), dk("2026-10-30T19:59"), dk("2026-11-02T04:00")])
    c = np.array([5.0, 5.5, 6.0])
    assert onceki_kapanis(dks, c, gun_bas_dk=dk("2026-11-02T04:00")) == 5.0


# ── ONAY ──
def _onay_seri(sonra_c, sonra_o=None):
    dks, o, h, l, c, v = _seri()
    sig = Sinyal("NASDAQ:X", "X", "BB", al_dk=int(dks[-1]), al_fiyat=float(c[-1]), gun_degisim=30.0)
    n = len(sonra_c)
    sonra_o = sonra_c if sonra_o is None else sonra_o
    return sig, (np.concatenate([dks, dks[-1] + 1 + np.arange(n)]), np.concatenate([o, sonra_o]),
                 np.concatenate([h, sonra_c]), np.concatenate([l, sonra_c]), np.concatenate([c, sonra_c]),
                 np.concatenate([v, np.full(n, 1000.0)]))


def test_ikinci_mumda_yukselis_surerse_onay():
    sig, d = _onay_seri(np.array([2.01, 2.08]), np.array([2.0, 2.02]))
    durum, neden = onay_durumu(sig, *d, min_artis=2.0)
    assert durum == "onay" and sig.onay_fiyat == 2.08 and "2. mumda" in neden


def test_ilk_mumdaki_yukselis_onay_sayilmaz():
    sig, d = _onay_seri(np.array([2.2]), np.array([2.0]))
    assert onay_durumu(sig, *d, min_artis=2.0)[0] == "bekliyor"


def test_al_fiyatinin_altina_inerse_iptal():
    sig, d = _onay_seri(np.array([1.95]))
    durum, neden = onay_durumu(sig, *d, min_artis=2.0)
    assert durum == "iptal" and "altına" in neden


def test_kirmizi_mumda_onay_yok():
    sig, d = _onay_seri(np.array([2.01, 2.08]), np.array([2.0, 2.12]))
    assert onay_durumu(sig, *d, min_artis=2.0)[0] == "bekliyor"


def test_dort_dakikada_onay_gelmezse_iptal():
    sig, d = _onay_seri(np.full(5, 2.005))
    assert onay_durumu(sig, *d, min_artis=2.0)[0] == "iptal"


def test_numpy_onay_mumu_backtest_kurali():
    c = np.array([2.0, 2.02, 2.06, 2.1])
    o = np.array([2.0, 2.0, 2.03, 2.06])
    g = S.Gostergeler(*(np.zeros(4),) * 9)
    assert S.onay_mumu(o, c, g, 0) == 2


def test_bollinger_ust_band_pine_ile_ayni():
    c = np.arange(1, 41, dtype=float)
    g = S.gostergeler(c, c, c, c, np.ones(40))
    beklenen = c[-20:].mean() + 2 * c[-20:].std()
    assert abs(g.bb_up[-1] - beklenen) < 1e-9


# ── onay sonrası: geri çekilme alışı, zarar silme, çıkış önerisi ──
from sistem.devam_takip import GERI_SURE_DK, onay_sonrasi  # noqa: E402


def _onayli():
    return Sinyal("NASDAQ:X", "X", "BB", al_dk=100, al_fiyat=2.0, gun_degisim=10.0, durum="onay",
                  onay_dk=102, onay_fiyat=2.0)


def _calistir(s, c, l=None, h=None, o=None, bas=103):
    c = np.asarray(c, float)
    dks = np.arange(bas, bas + len(c))
    l = c if l is None else np.asarray(l, float)
    h = c if h is None else np.asarray(h, float)
    o = c if o is None else np.asarray(o, float)
    onay_sonrasi(s, dks, o, h, l, c)
    return s


def test_geri_cekilme_seviyesine_inince_doldu_ve_kart_kalir():
    s = _calistir(_onayli(), [1.95, 1.88, 1.86], l=[1.94, 1.83, 1.85])
    assert s.durum == "onay" and s.geri_durum == "doldu" and s.geri_seviye == 1.84
    assert s.geri_fiyat == 1.84 and s.geri_dk == 104


def test_geri_cekilme_sonrasi_hedef():
    s = _calistir(_onayli(), [1.9, 1.86, 1.95], l=[1.9, 1.83, 1.9], h=[1.9, 1.87, 1.94])
    assert s.geri_durum == "hedef" and s.geri_sonuc_dk == 105 and s.durum == "onay"


def test_geri_cekilme_sonrasi_stop_karti_siler():
    s = _calistir(_onayli(), [1.9, 1.86, 1.68], l=[1.9, 1.83, 1.6])
    assert s.geri_durum == "stop" and s.durum == "zarar"


def test_pencere_icinde_dusus_silinmez_bekler():
    s = _calistir(_onayli(), [1.93, 1.9])
    assert s.durum == "onay" and s.geri_durum == "bekliyor"


def test_pencere_bitince_geri_cekilme_yoksa_eski_zarar_kurali():
    c = [2.05] * GERI_SURE_DK + [1.89]
    s = _calistir(_onayli(), c)
    assert s.geri_durum == "yok" and s.durum == "zarar"


def test_hafif_sarkma_silinmez_ve_hedefte_cik_onerisi():
    s = _calistir(_onayli(), [1.99] + [2.05] * GERI_SURE_DK + [2.21], h=[1.99] + [2.05] * GERI_SURE_DK + [2.22])
    assert s.durum == "onay" and s.geri_durum == "yok" and "hedef" in s.cikis


# ── SAT / SÜRE izleme ──
def test_sat_bul_sure_ve_sat():
    from sistem.devam_takip import sat_bul, SURE_MUM
    c = np.full(30, 2.0)
    g = S.Gostergeler(*(np.zeros(30),) * 9)          # vw/e50/atr = 0 → SAT yok
    assert sat_bul(0, c, g, np.arange(30)) == ("sure", SURE_MUM)
    assert sat_bul(0, c[:5], g, np.arange(5)) == ("devam", 4)
    g2 = S.Gostergeler(*(np.full(30, 3.0),) * 9)     # kapanış VWMA ve EMA50 altında → 1. mumda SAT
    assert sat_bul(0, c, g2, np.arange(30)) == ("sat", 1)


def test_onceki_kapanis_tarayicidan():
    from sistem.devam_takip import onceki_tarayicidan
    assert onceki_tarayicidan({"close": 5.0, "change": 3.0}, premarket=True) == 5.0
    assert abs(onceki_tarayicidan({"close": 6.0, "change": 20.0}, premarket=False) - 5.0) < 1e-9
    assert onceki_tarayicidan({"close": None}, premarket=False) is None


def test_guvenli_degistir_kilitliyse_cokmez(tmp_path, monkeypatch):
    from pathlib import Path
    from sistem import devam_takip as DT
    tmp, hedef = tmp_path / "a.tmp", tmp_path / "a.json"
    tmp.write_text("{}")
    monkeypatch.setattr(Path, "replace", lambda self, t: (_ for _ in ()).throw(PermissionError("kilitli")))
    monkeypatch.setattr(DT._time, "sleep", lambda s: None)
    assert DT.guvenli_degistir(tmp, hedef) is False
