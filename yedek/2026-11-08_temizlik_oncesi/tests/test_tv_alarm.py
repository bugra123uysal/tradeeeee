from datetime import date

from sistem import tv_alarm as A

GUN = date(2026, 11, 3)


def _fire(msg, bar="2026-11-03T15:15:00Z", fire="2026-11-03T15:17:00Z", sym="NASDAQ:CHSN"):
    return {"message": msg, "bar_time": bar, "fire_time": fire, "symbol": "BATS:X",
            "pro_symbol": '={"symbol":"%s","adjustment":"splits","session":"extended"}' % sym}


def test_yuksek_sinyal_mesaji_ayiklanir():
    out = A.ayikla([_fire("yüksek: SİNYAL CHSN @ 1.1300 · geri çekilme alışı 1.0735")], GUN)
    assert len(out) == 1
    x = out[0]
    assert (x.sym, x.fiyat, x.seviye) == ("NASDAQ:CHSN", 1.13, 1.0735)
    assert x.al_dk * 60 == 1793718900  # 2026-11-03T15:15:00Z


def test_diger_alarmlar_atlanir():
    fires = [_fire("Çoklu Swing 5dk: SAT EHGO @ 2.35"),
             _fire("yüksek: GERİ ÇEKİLME TAMAM — AL CHSN @ 1.07")]
    assert A.ayikla(fires, GUN) == []


def test_baska_gunun_alarmi_atlanir():
    f = _fire("yüksek: SİNYAL CHSN @ 1.13 · geri çekilme alışı 1.07", bar="2026-11-02T15:15:00Z")
    assert A.ayikla([f], GUN) == []


def test_iki_listeden_ayni_sinyal_bir_kez():
    msg = "yüksek: SİNYAL CHSN @ 1.13 · geri çekilme alışı 1.07"
    out = A.ayikla([_fire(msg, fire="2026-11-03T15:18:00Z"), _fire(msg)], GUN)
    assert len(out) == 1 and out[0].tetik == 1793719020.0
