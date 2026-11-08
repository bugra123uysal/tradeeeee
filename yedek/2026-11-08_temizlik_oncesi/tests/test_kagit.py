"""sistem/kagit.py — Alpaca çağrıları sahte (_istek yerine kayıt tutan fonksiyon); gerçek emir gitmez."""
import time
from datetime import datetime

import pytest

from sistem import geri_al as G
from sistem import kagit as K
from sistem.liste_yenile import NY

SIMDI_NY = datetime(2026, 11, 4, 10, 30, tzinfo=NY)


class SahteAlpaca:
    def __init__(self, hesap="PA123"):
        self.hesap, self.emirler, self.cagrilar = hesap, {}, []

    def __call__(self, yontem, yol, **kw):
        self.cagrilar.append((yontem, yol, kw.get("json")))
        if yol == "/account":
            return {"account_number": self.hesap}
        if yontem == "POST":
            oid = f"o{len(self.emirler) + 1}"
            self.emirler[oid] = {"status": "new", **kw["json"]}
            return {"id": oid}
        if yontem == "GET":
            return self.emirler[yol.split("/")[-1]]
        if yontem == "DELETE":
            self.emirler[yol.split("/")[-1]]["status"] = "canceled"
            return None


@pytest.fixture
def hesap(tmp_path, monkeypatch):
    api = SahteAlpaca()
    monkeypatch.setattr(K.KagitHesap, "_istek", lambda self, *a, **kw: api(*a, **kw))
    monkeypatch.setattr(K.KagitHesap, "_kota", lambda self, sym: (1.0, 1.01))
    monkeypatch.setenv("KAGIT_ACIK", "1")
    monkeypatch.setenv("KAGIT_USD", "1000")
    h = K.KagitHesap(tmp_path / "kagit.json")
    h.yukle("2026-11-04")
    return h, api


def _sinyal(gun=10.0, yas_dk=1, sym="NASDAQ:ABC", fiyat=2.0):
    al_dk = int(time.time() // 60) - yas_dk
    return G.GeriSinyal(sym, "", "TV", al_dk, fiyat, gun, seviye=fiyat * 0.95)


def test_sinyalde_limit_alis_emri_verilir(hesap):
    h, api = hesap
    h.adim([_sinyal()], {}, SIMDI_NY)
    emir = api.emirler["o1"]
    assert (emir["side"], emir["type"], emir["limit_price"], emir["qty"]) == ("buy", "limit", "1.9", "526")
    assert h.islemler[0].durum == "emir"


def test_gun_filtresine_takilan_ve_eski_sinyal_islenmez(hesap):
    h, api = hesap
    h.adim([_sinyal(gun=25.0), _sinyal(yas_dk=45, sym="NASDAQ:XYZ")], {}, SIMDI_NY)
    assert api.emirler == {} and h.islemler == []


def _doldur(api, oid, fiyat):
    api.emirler[oid].update(status="filled", filled_avg_price=str(fiyat), filled_qty=api.emirler[oid]["qty"])


def test_dolunca_yari_yari_hedef_emirleri_sonra_kapanir(hesap):
    h, api = hesap
    h.adim([_sinyal()], {}, SIMDI_NY)                       # 526 adet @ 1.90
    _doldur(api, "o1", 1.9)
    h.adim([], {}, SIMDI_NY)
    x = h.islemler[0]
    assert x.durum == "doldu"
    assert (api.emirler["o2"]["qty"], api.emirler["o2"]["limit_price"]) == ("263", "2.0")    # yarısı +%5 (yukarı)
    assert (api.emirler["o3"]["qty"], api.emirler["o3"]["limit_price"]) == ("263", "2.28")   # kalan +%20
    _doldur(api, "o2", 1.995)
    h.adim([], {}, SIMDI_NY)
    assert x.durum == "doldu" and x.yari_zaman is not None and x.satilan == 263
    _doldur(api, "o3", 2.28)
    h.adim([], {}, SIMDI_NY)
    assert x.durum == "kapandi" and x.cikis_neden == "hedef" and x.kar_yuzde == pytest.approx(12.5)


def test_otuz_dakikada_dolmazsa_iptal(hesap):
    h, api = hesap
    h.adim([_sinyal()], {}, SIMDI_NY)
    h.islemler[0].emir_zaman -= 31 * 60
    h.adim([], {}, SIMDI_NY)
    assert h.islemler[0].durum == "iptal" and api.emirler["o1"]["status"] == "canceled"


def test_stop_kapanisinda_hedefler_iptal_satis(hesap):
    h, api = hesap
    s = _sinyal()
    h.adim([s], {}, SIMDI_NY)
    _doldur(api, "o1", 1.9)
    h.adim([], {}, SIMDI_NY)
    dk = int(time.time() // 60)
    h.adim([], {s.sym: (dk, 1.70)}, SIMDI_NY)                  # 1.70 < 1.9 × 0.92
    x = h.islemler[0]
    assert api.emirler["o2"]["status"] == "canceled" and api.emirler["o3"]["status"] == "canceled"
    assert (api.emirler["o4"]["side"], api.emirler["o4"]["type"], api.emirler["o4"]["qty"]) == ("sell", "market", "526")
    _doldur(api, "o4", 1.69)
    h.adim([], {}, SIMDI_NY)
    assert x.durum == "kapandi" and x.cikis_neden == "stop" and x.kar_usd < 0


def test_yarisi_satildiktan_sonra_stop_kalan_yariyi_satar(hesap):
    h, api = hesap
    s = _sinyal()
    h.adim([s], {}, SIMDI_NY)
    _doldur(api, "o1", 1.9)
    h.adim([], {}, SIMDI_NY)
    _doldur(api, "o2", 1.995)
    h.adim([], {}, SIMDI_NY)
    h.adim([], {s.sym: (int(time.time() // 60), 1.70)}, SIMDI_NY)
    x = h.islemler[0]
    assert api.emirler["o4"]["qty"] == "263"
    _doldur(api, "o4", 1.70)
    h.adim([], {}, SIMDI_NY)
    assert x.durum == "kapandi" and x.cikis_neden == "stop"
    assert x.kar_yuzde == pytest.approx(((1.995 - 1.9) + (1.70 - 1.9)) / 2 / 1.9 * 100)


def test_fiyat_yuvarlama():
    assert K._fiyat(1.995, yukari=True) == 2.0 and K._fiyat(1.995) == 1.99
    assert K._fiyat(0.35625) == 0.3562 and K._fiyat(0.35625, yukari=True) == 0.3563


def test_kagit_olmayan_hesapta_calismaz(tmp_path, monkeypatch):
    api = SahteAlpaca(hesap="123456")
    monkeypatch.setattr(K.KagitHesap, "_istek", lambda self, *a, **kw: api(*a, **kw))
    h = K.KagitHesap(tmp_path / "k.json")
    h.adim([_sinyal()], {}, SIMDI_NY)
    assert not h.acik and not any(c[0] == "POST" for c in api.cagrilar)


def test_en_cok_acik_pozisyon_siniri(hesap, monkeypatch):
    h, api = hesap
    h.max_acik = 1
    h.adim([_sinyal(sym="NASDAQ:A"), _sinyal(sym="NASDAQ:B")], {}, SIMDI_NY)
    assert len(api.emirler) == 1


def test_gun_sonu_kapanisindan_sonra_yeni_alis_emri_verilmez(hesap):
    h, api = hesap
    h.adim([_sinyal()], {}, datetime(2026, 11, 4, 15, 56, tzinfo=NY))   # alınsa hemen "gün sonu" satılırdı
    assert api.emirler == {} and h.islemler == []
