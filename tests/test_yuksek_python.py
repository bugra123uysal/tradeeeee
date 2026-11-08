"""Tüm piyasa Python 'yüksek' taraması (8 Kas 2026) + GİR kalite etiketi (OBV > EMA20 · Kijun üstü)."""
import time as _time
from datetime import datetime

import numpy as np

from sistem import geri_al as G
from sistem import yuksek as Y
from sistem.devam_takip import Mumlar, Takipci


def _dk(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=Y.NY).timestamp() // 60)


def test_kalite_yukselen_seride_obv_ve_kijun_ustu():
    c = np.linspace(2.0, 3.0, 60)
    obv, kij = Y.kalite(c + 0.01, c - 0.01, c, np.full(60, 1000.0))
    assert obv is True and kij is True


def test_kalite_dusen_seride_ikisi_de_hayir():
    c = np.linspace(3.0, 2.0, 60)
    obv, kij = Y.kalite(c + 0.01, c - 0.01, c, np.full(60, 1000.0))
    assert obv is False and kij is False


def test_kalite_az_mumda_bilinmiyor():
    c = np.ones(10)
    assert Y.kalite(c, c, c, c) == (None, None)


def _takipci(al_dk: int, son_dk: int, onceki: float = 2.0) -> Takipci:
    t = object.__new__(Takipci)
    m = Mumlar()
    for d in range(al_dk - 40, son_dk + 1):
        m.alpaca_ekle(d, 2.2, 2.25, 2.15, 2.2, 1000)
    t.mumlar, t.yuksek, t.onceki, t.bilgi = {"NASDAQ:X": m}, [], {"NASDAQ:X": onceki}, {}
    t.gun_bas_dk, t.py_bitti = al_dk - 300, set()
    return t


def test_python_sinyali_taze_ve_uygunsa_eklenir(monkeypatch):
    al = _dk("2026-11-06T12:40")
    t = _takipci(al, al + 2)
    monkeypatch.setattr(Y, "yuksek_sinyalleri", lambda dks, *a, **k: [int(np.searchsorted(dks, al))])
    monkeypatch.setattr(_time, "time", lambda: (al + 3) * 60.0)
    t._python_sinyal("NASDAQ:X", al + 2)
    assert len(t.yuksek) == 1 and t.yuksek[0].tur == "PY" and t.yuksek[0].seviye == round(2.2 * 0.95, 6)


def test_python_sabah_sinyali_eklenmez(monkeypatch):
    al = _dk("2026-11-06T10:05")
    t = _takipci(al, al + 2)
    monkeypatch.setattr(Y, "yuksek_sinyalleri", lambda dks, *a, **k: [int(np.searchsorted(dks, al))])
    t._python_sinyal("NASDAQ:X", al + 2)
    assert t.yuksek == [] and "NASDAQ:X" in t.py_bitti


def test_python_eski_sinyal_eklenmez(monkeypatch):
    al = _dk("2026-11-06T12:00")
    t = _takipci(al, al + 45)                     # takipçi hisseyi 45 dk sonra gördü: geri çekilme penceresi geçti
    monkeypatch.setattr(Y, "yuksek_sinyalleri", lambda dks, *a, **k: [int(np.searchsorted(dks, al))])
    t._python_sinyal("NASDAQ:X", al + 45)
    assert t.yuksek == [] and "NASDAQ:X" in t.py_bitti


def test_tv_alarmi_olan_hisseye_python_ikinci_sinyal_eklemez(monkeypatch):
    al = _dk("2026-11-06T12:40")
    t = _takipci(al, al + 2)
    t.yuksek.append(G.GeriSinyal("NASDAQ:X", "", "TV", al - 5, 2.2, 10.0, seviye=2.09))
    monkeypatch.setattr(Y, "yuksek_sinyalleri", lambda dks, *a, **k: [int(np.searchsorted(dks, al))])
    t._python_sinyal("NASDAQ:X", al + 2)
    assert len(t.yuksek) == 1 and t.yuksek[0].tur == "TV"


def test_gir_mesajinda_kalite_ve_kaynak():
    from sistem import bildirim as B
    x = G.GeriSinyal("NASDAQ:ABC", "", "PY", 29000000, 2.0, 12.0, seviye=1.9, durum="al", dolum_dk=29000010,
                     dolum_fiyat=1.9, karar=True, kirmizi=0.25, obv_ustu=True, kijun_ustu=False)
    m = B.gir_mesaji(x)
    assert "Kalite B" in m and "OBV ✓" in m and "Kijun ✗" in m and "Python" in m
