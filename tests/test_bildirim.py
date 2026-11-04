"""sistem/bildirim.py — GİR ve Yükselme Adayları Telegram mesajları: bir kez, taze, ALMA filtresi."""
from sistem import bildirim as B
from sistem import geri_al as G
from sistem import yukselme as YU

SIMDI = 1_000_000 * 60.0


def _gir(dolum_dk=1_000_000 - 2, gun=12.0, durum="al"):
    return G.GeriSinyal("NASDAQ:ABC", "", "TV", 1_000_000 - 10, 2.0, gun, seviye=1.9, durum=durum,
                        dolum_dk=dolum_dk, dolum_fiyat=1.9)


def _bild(tmp_path, gun="2026-11-04"):
    giden = []
    return B.Bildirici(giden.append, tmp_path / "g.json", gun), giden


def test_gir_bir_kez_gider_ve_seviyeleri_yazar(tmp_path):
    b, giden = _bild(tmp_path)
    b.yuksek([_gir()], SIMDI); b.yuksek([_gir()], SIMDI)
    assert len(giden) == 1 and "GİR: ABC" in giden[0] and "1.995" in giden[0] and "1.748" in giden[0]


def test_yeniden_baslatmada_tekrar_gitmez(tmp_path):
    b, _ = _bild(tmp_path); b.yuksek([_gir()], SIMDI)
    b2, giden2 = _bild(tmp_path); b2.yuksek([_gir()], SIMDI)
    assert giden2 == []


def test_eski_dolum_ve_alma_filtresi_gitmez(tmp_path):
    b, giden = _bild(tmp_path)
    b.yuksek([_gir(dolum_dk=1_000_000 - 60)], SIMDI)          # 1 saat önce dolmuş
    b.yuksek([G.GeriSinyal("NASDAQ:XYZ", "", "TV", 5, 2.0, 35.0, seviye=1.9, durum="al",
                           dolum_dk=1_000_000 - 1, dolum_fiyat=1.9)], SIMDI)   # gün > 20 → ALMA
    assert giden == []


def test_yeni_gun_dosyasi_sifirlanir(tmp_path):
    b, _ = _bild(tmp_path, "2026-11-03"); b.yuksek([_gir()], SIMDI)
    b2, giden2 = _bild(tmp_path, "2026-11-04"); b2.yuksek([_gir()], SIMDI)
    assert len(giden2) == 1


def test_yukselme_listeye_giris_ve_devam_adayi(tmp_path):
    b, giden = _bild(tmp_path)
    a = YU.Aday("NASDAQ:FTCI", ad="FTC <Solar>", fiyat=1.7, pm_degisim=25.0, karar="İZLE", neden="bekliyor",
                haberler=[{"baslik": "Q3 sonuçları"}])
    b.yukselme([a]); b.yukselme([a])
    a.karar = "DEVAM ADAYI"; b.yukselme([a]); b.yukselme([a])
    assert len(giden) == 2 and "listeye girdi" in giden[0] and "DEVAM ADAYI" in giden[1]
    assert "&lt;Solar&gt;" in giden[0] and "Q3" in giden[0]


def test_haber_yoksa_mesajda_yazar(tmp_path):
    b, giden = _bild(tmp_path)
    b.yukselme([YU.Aday("NASDAQ:WORX", karar="GEÇ", neden="Haber yok")])
    assert "Haber yok" in giden[0]
