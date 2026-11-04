import json

import numpy as np

from sistem import sinyal as S
from sistem.gunluk_takip import _sembol, evren, hisse_sinyalleri, strateji


def _bars(c, o=None):
    o = c if o is None else o
    return [{"t": f"2026-01-{i % 28 + 1:02d}T05:00:00Z" if i < 28 else f"2026-{2 + i // 28:02d}-{i % 28 + 1:02d}T05:00:00Z",
             "o": float(o[i]), "h": float(max(o[i], c[i]) + 0.002), "l": float(min(o[i], c[i]) - 0.002),
             "c": float(c[i]), "v": 1000.0} for i in range(len(c))]


def _yatay(n=80):
    return np.full(n, 20.0) + np.sin(np.arange(n)) * 0.01


def test_sembol_alpaca_bicimi():
    assert _sembol("NYSE:BRK-B") == "BRK.B" and _sembol(" aapl ") == "AAPL"


def test_evren_config_ve_dosya(tmp_path):
    (tmp_path / "liste.txt").write_text("# yorum\nAAPL, MSFT\nNVDA\n", encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"symbols": ["NVDA"], "coklu_symbols": ["TSLA"],
                               "symbol_files": [{"path": "liste.txt", "group": "SP"}]}), encoding="utf-8")
    assert evren(cfg) == {"NVDA": "Test", "TSLA": "Çoklu", "AAPL": "SP", "MSFT": "SP"}


def test_strateji_pozisyondayken_yeni_al_yok():
    c = np.concatenate([_yatay(), [21.0, 22.0, 23.0, 24.0]])
    g = S.gostergeler(c, c, c, c, np.ones(len(c)))
    isl = strateji(c, g)
    assert len(isl) == 1 and isl[0].al_i == 80 and isl[0].cikis_i is None


def test_strateji_sat_ile_kapanir():
    c = np.concatenate([_yatay(), [21.0, 15.0]])
    g = S.gostergeler(c, c, c, c, np.ones(len(c)))
    isl = strateji(c, g)
    assert isl[0].cikis in ("SAT", "STOP") and isl[0].cikis_i == 81


def test_onay_karari_min_artis_parametresi():
    c = np.array([10.0, 10.1, 10.25])
    g = S.Gostergeler(*(np.zeros(3),) * 9)
    assert S.onay_karari(c, c, g, 0, min_artis=3.0)[0] == "bekliyor"
    durum, j, _ = S.onay_karari(c, c, g, 0, min_artis=2.4)
    assert durum == "onay" and j == 2


def test_hisse_sinyalleri_onay_ve_gecici():
    c = np.concatenate([_yatay(), [21.0, 21.3, 21.8]])
    o = np.concatenate([_yatay(), [20.0, 21.0, 21.3]])
    bars = _bars(c, o)
    out = hisse_sinyalleri("X", "SP", bars, 2.0, gecici_tarih=bars[-1]["t"][:10])
    assert len(out) == 1
    s = out[0]
    assert s["durum"] == "onay" and s["onay_fiyat"] == 21.8 and s["gecici"] and s["onaydan_gun"] == 0


def test_hisse_sinyalleri_iptal_gosterilmez():
    c = np.concatenate([_yatay(), [21.0, 20.9]])
    assert hisse_sinyalleri("X", "", _bars(c), 2.0, None) == []


def test_hisse_sinyalleri_bekleyen():
    c = np.concatenate([_yatay(), [21.0, 21.1]])
    s = hisse_sinyalleri("X", "", _bars(c), 2.0, None)
    assert [x["durum"] for x in s] == ["bekliyor"]


def test_onaydan_sonra_giris_altina_inen_gosterilmez():
    c = np.concatenate([_yatay(), [21.0, 21.3, 21.8, 21.5]])
    o = np.concatenate([_yatay(), [20.0, 21.0, 21.3, 21.6]])
    assert hisse_sinyalleri("X", "", _bars(c, o), 2.0, None) == []


def test_eklenme_ilk_gorulen_an_korunur():
    from sistem.gunluk_takip import eklenme_isle
    s1 = [{"sym": "A", "al_tarih": "2026-11-02", "onay_tarih": "2026-11-04", "durum": "onay"},
          {"sym": "B", "al_tarih": "2026-10-20", "onay_tarih": "2026-10-22", "durum": "onay"}]
    m = eklenme_isle(s1, {}, 100.0, "2026-11-02")
    assert s1[0]["eklendi"] == 100.0 and s1[1]["eklendi"] is None
    s2 = [{"sym": "A", "al_tarih": "2026-11-02", "onay_tarih": "2026-11-04", "durum": "onay"}]
    eklenme_isle(s2, m, 999.0, "2026-11-02")
    assert s2[0]["eklendi"] == 100.0
