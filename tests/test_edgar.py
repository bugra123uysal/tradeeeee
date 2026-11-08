from datetime import datetime

import pytest

from sistem import edgar as E


@pytest.mark.parametrize("form,beklenen", [
    ("S-3", "kayit"), ("S-1/A", "kayit"), ("F-3ASR", "kayit"), ("424B5", "ihrac"), ("424B3", "ihrac"),
    ("SC 13D", "buyuk_ortak"), ("SCHEDULE 13G/A", "buyuk_ortak"), ("4", "iceriden"), ("4/A", "iceriden"),
    ("8-K", "olay"), ("10-Q", None), ("S-8", None), ("40-F", None),
])
def test_kategori(form, beklenen):
    assert E.kategori(form) == beklenen


SUB = {"filings": {"recent": {
    "form": ["8-K", "10-Q", "424B5", "S-3"],
    "accessionNumber": ["0001-26-000003", "0001-26-000002", "0001-26-000001", "0001-25-000009"],
    "acceptanceDateTime": ["2026-11-05T22:10:14.000Z", "2026-11-01T12:00:00.000Z",
                           "2026-10-20T13:00:00.000Z", "2026-03-01T15:00:00.000Z"],
    "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm"],
}}}


def test_dosyalar_saat_ve_url():
    l = E.dosyalar(SUB, 1557798)
    assert [d.form for d in l] == ["S-3", "424B5", "8-K"]                  # 10-Q atlandı, eskiden yeniye
    assert l[-1].zaman == datetime(2026, 11, 5, 17, 10, 14, tzinfo=E.NY)   # UTC → NY (kış saati)
    assert l[-1].url.endswith("/1557798/000126000003/a.htm")


def test_ozet_ileriye_bakmaz():
    l = E.dosyalar(SUB, 1)
    an = datetime(2026, 11, 5, 9, 30, tzinfo=E.NY)
    assert E.ozet(l, an, 30) == {"kayit": 0, "ihrac": 1, "buyuk_ortak": 0, "iceriden": 0, "olay": 0}
    assert E.ozet(l, datetime(2026, 11, 6, 9, 30, tzinfo=E.NY), 1)["olay"] == 1
    assert E.ozet(l, an, 365)["kayit"] == 1


def test_eposta_zorunlu(tmp_path):
    with pytest.raises(ValueError):
        E.Edgar(tmp_path, ua="sadece-ad")
