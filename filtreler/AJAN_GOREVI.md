# Ajan görevi — yükselen hisse listelerini yenile

Bu dosya terminaldeki ajanlara verilecek görev tanımıdır.

**1 Kasım 2026: kodlandı** → `sistem/liste_yenile.py` (`liste_yenile.bat`, zamanlama için `gorev_kur.ps1`).
Listeler "###1 Daha sakin" / "###2 Sönme riski yüksek" bölümlerine ayrılır (kurallar dosyanın başında,
dayanağı `backtest/` — 1 Eyl–30 Eki 2026 testi). Yeni girenler `filtreler/liste_log.csv`'ye yazılır.

31 Ekim 2026: **bugraaa_premarket** (41 hisse) ve **bugraaa_trade** (53 hisse) izleme listeleri
TradingView'da oluşturuldu ve bu dosyadaki JSON filtrelerinin sonucuyla bir kez dolduruldu.

## TradingView'daki karşılıkları (30 Ekim 2026'da kuruldu)

Aynı filtreler TradingView Hisse Tarayıcısı'nda da kayıtlı, elle bakmak için:

| Tarama | Filtreler |
|---|---|
| **bugraaa_premarket** | Fiyat > 1 $ · Pys öncesi değişim > %5 · Pys öncesi hacim > 5 bin · Borsa: NASDAQ, NYSE, NYSE Arca |
| **bugraaa_trade** | Fiyat > 1 $ · Değ > %5 · Göreceli hacim > 1,5 · Hacim > 500 bin · Borsa: NASDAQ, NYSE, NYSE Arca |

⚠ 31 Ekim: JSON filtrelerinde fiyat **0,20 $**'a, premarket hacmi **2 bin**'e indirildi (1 $ altı dahil).
TradingView'daki iki tarama hâlâ eski eşiklerle (fiyat > 1 $, premarket hacmi > 5 bin). Aynı listeyi
TradingView'da da görmek istersen taramalarda bu iki değeri elle değiştir.
| cuma_çevik (değiştirilmedi) | Fiyat 5–2000 $ · Analist notu (3 seçenek) · Pys öncesi değişim > %5 |

İki küçük fark: TradingView taramasında premarket'te "Fiyat" dünün kapanışıdır, JSON dosyası ise
premarket fiyatına bakar. Borsa seçiminde TradingView'da AMEX ayrı seçenek değil.
Bu yüzden listeler 2–4 hisse farklı çıkabilir (30 Ekim: TradingView 28, JSON 24 hisse).
Ajanlar JSON dosyalarını kullanır.

## Ne yapacak

Her çalıştırmada:

1. Saate göre doğru filtreyi seç (TR saati; ABD kış saatinde +1 saat kaydır):
   - Premarket (11:00–16:30 TR, kışın 12:00–17:30): `premarket.json`
   - Piyasa saati (16:30–23:00 TR, kışın 17:30–00:00): `piyasa.json`
2. tradingview.com açık bir sekmeden filtreyi gönder:
   `POST https://scanner.tradingview.com/america/scan`
   Gövde: JSON dosyasının içeriği (`_` ile başlayan alanlar çıkarılır).
   Başlık `Content-Type: text/plain`, `credentials: "include"`.
   Sonuç: `data[].s` = "BORSA:SEMBOL", `data[].d` = `columns` sırasıyla değerler.
3. Sonucu TradingView izleme listesine yaz (eski içerik tamamen değiştirilir):
   - Premarket sonucu → **bugraaa_premarket** listesi (ASIL ÖNEMLİ LİSTE)
   - Piyasa saati sonucu → **bugraaa_trade** listesi
   - **bugraaa_premarket açılışta SİLİNMEZ:** son premarket taraması (açılıştan hemen önce,
     16:25 TR / kışın 17:25) gün boyu listede kalır. Piyasa saatinde sadece bugraaa_trade yenilenir.
     Ertesi gün premarket başlayınca bugraaa_premarket yeniden yazılmaya başlar.
   - Liste yoksa oluştur. Diğer izleme listelerine **dokunma**.
   - API (aynı sekmeden, göreli yol, credentials include):
     - Listeleri al: `GET /api/v1/symbols_list/custom/`
     - Tümünü değiştir: `POST /api/v1/symbols_list/custom/{id}/replace/?unsafe=true`
       gövde: `["NASDAQ:GDYN", "NYSE:CPRI", ...]` (`unsafe=true` olmadan 422 döner)
4. Bir önceki çalıştırmaya göre **listeye yeni giren** sembolleri bul ve bildir
   (Telegram veya push). Örnek mesaj: `🆕 bugraaa_trade: GDYN +32,6% · hacim 3,7x`.
5. Önceki listeyi bir sonraki karşılaştırma için sakla (`son_liste_premarket.json`,
   `son_liste_piyasa.json`).

## Sıklık

- Premarket: her 5 dakikada bir
- Piyasa saati: her 2–5 dakikada bir
- Hafta sonu ve ABD tatillerinde çalışma

## Ayarlanabilir eşikler

| Ne | Premarket | Piyasa saati |
|---|---|---|
| Fiyat | premarket fiyatı > 0,20 $ (1 $ altı dahil) | > 0,20 $ (1 $ altı dahil) |
| Değişim | premarket > %5 | gün içi > %5 |
| Hacim | premarket > 2 bin | > 500 bin |
| Göreceli hacim | — | > 1,5 |
| Borsa | NASDAQ, NYSE, AMEX (OTC hariç) | aynı |

## 1 $ altı eşik karşılaştırması (30 Ekim verisi)

| Premarket ayarı | Hisse | Gün sonu > +%5 | Gün sonu eksi | ONEG |
|---|---|---|---|---|
| fiyat > 1 $, hacim > 5 bin | 24 | 19 | 4 | yok |
| fiyat > 0,50 $, hacim > 2 bin | 35 | 24 | 10 | var |
| **fiyat > 0,20 $, hacim > 2 bin (seçilen)** | **41** | **26** | **14** | var |

1 $ altında listeye girenler: ONEG +108, SLGB +49, FRGT +14, ACH +9, XCUR +8 ama
BURU −23, INO −22, PLAG −11, MGRX −8, AZIO −6, NMRA −5. Kazanç büyük, kayıp sayısı da fazla.

## 30 Ekim 2026 deneme sonucu (ilk ayarlarla)

**Premarket (5 bin hacim eşiğiyle): 24 hisse.** Gün içinde ne yaptıkları:
- Yükselişi sürdürenler (17): ASUR +35, GDYN +35, FMC +27, TNMG +27, AIOK +28, OSPN +25,
  RMNI +23, SEGG +23, ACCO +21, CDNA +19, GITS +17, CVEO +16, WKHS +15, CPRI +15,
  FDXF +15, REGN +12, BTSG +9
- Geri verenler (7): VIVK −37, NRXP −14, WFF −11, NCPL −8, STRZ −2, RIVN +3, RKT +7
  → Geri verenlerin çoğu 1–3 $ arası, premarket'te milyonlarca adet işlem görmüş hisseler.
    Bunlara dikkat: çok erken patlayıp gün içinde satılabiliyor.

İlk denemede eşik 50 bin adetti ve bu 17 kazananın 11'ini (GDYN, ASUR, OSPN, CDNA, ACCO…)
eledi. Premarket'te hacim doğal olarak düşük olduğu için eşik 5 bine indirildi.

**Piyasa saati: 21 hisse.** Bunların 7'si (CHOW, NFE, GPN, CBAT…) premarket'te hiç
yükselmemişti, açılıştan sonra hareket etti — onları yalnızca piyasa saati listesi yakalar.
