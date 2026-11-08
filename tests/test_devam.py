"""sistem/devam_takip.py — canlı mum kurucu, önceki kapanış, güvenli dosya yazımı; sistem/sinyal.py göstergeleri.
(8 Kas 2026: eski "AL + 2–4 mum onayı" ve "Pine AL geri çekilme" takipçileri ve testleri kaldırıldı.)"""
import numpy as np

from sistem import sinyal as S
from sistem.devam_takip import Mumlar, onceki_kapanis


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
    dks, *_, c, v = m.diziler(10)
    assert list(dks) == [10] and list(c) == [2.0]


# ── önceki kapanış ──
def test_onceki_kapanis_gece_seansini_atlar():
    from datetime import datetime
    from sistem.liste_yenile import NY
    dk = lambda s: int(datetime.fromisoformat(s).replace(tzinfo=NY).timestamp() // 60)
    dks = np.array([dk("2026-10-30T15:59"), dk("2026-10-30T19:59"), dk("2026-11-02T04:00")])
    c = np.array([5.0, 5.5, 6.0])
    assert onceki_kapanis(dks, c, gun_bas_dk=dk("2026-11-02T04:00")) == 5.0


def test_onceki_kapanis_tarayicidan():
    from sistem.devam_takip import onceki_tarayicidan
    assert onceki_tarayicidan({"close": 5.0, "change": 3.0}, premarket=True) == 5.0
    assert abs(onceki_tarayicidan({"close": 6.0, "change": 20.0}, premarket=False) - 5.0) < 1e-9
    assert onceki_tarayicidan({"close": None}, premarket=False) is None


# ── göstergeler (sistem/sinyal.py, backtestlerle ortak) ──
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


def test_guvenli_degistir_kilitliyse_cokmez(tmp_path, monkeypatch):
    from pathlib import Path
    from sistem import devam_takip as DT
    tmp, hedef = tmp_path / "a.tmp", tmp_path / "a.json"
    tmp.write_text("{}")
    monkeypatch.setattr(Path, "replace", lambda self, t: (_ for _ in ()).throw(PermissionError("kilitli")))
    monkeypatch.setattr(DT._time, "sleep", lambda s: None)
    assert DT.guvenli_degistir(tmp, hedef) is False
