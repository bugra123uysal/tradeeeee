from datetime import datetime, time
from zoneinfo import ZoneInfo

from sistem import video_ekran as V

NY = ZoneInfo("America/New_York")
C = (V.Cekim("a", time(10, 0), "/a", "x.json"), V.Cekim("b", time(15, 50), "/", "x.json"))


def test_saati_gelince_bekler(tmp_path):
    e = V.EkranAlici(cekimler=C, dizin=tmp_path)
    assert e.bekleyenler(datetime(2026, 11, 6, 9, 59, tzinfo=NY)) == []
    assert [c.ad for c in e.bekleyenler(datetime(2026, 11, 6, 10, 0, tzinfo=NY))] == ["a"]
    assert [c.ad for c in e.bekleyenler(datetime(2026, 11, 6, 15, 55, tzinfo=NY))] == ["b"]   # a'nın penceresi geçti


def test_dosya_varsa_veya_denendiyse_tekrar_yok(tmp_path):
    e = V.EkranAlici(cekimler=C, dizin=tmp_path)
    (tmp_path / "2026-11-06").mkdir()
    (tmp_path / "2026-11-06" / "site_a.png").write_bytes(b"x")
    assert e.bekleyenler(datetime(2026, 11, 6, 10, 5, tzinfo=NY)) == []
    e._denendi.add(("2026-11-06", "b"))
    assert e.bekleyenler(datetime(2026, 11, 6, 16, 0, tzinfo=NY)) == []
    assert [c.ad for c in e.bekleyenler(datetime(2026, 11, 9, 16, 0, tzinfo=NY))] == ["b"]   # ertesi gün yeniden
