# Otomatik işlem kuralları (Alpaca) — taslak

Bu dosya ajanlara verilecek tasarım. Kod henüz yazılmadı. Önce **paper hesapta** çalışacak.

## Akış

```
bugraaa_premarket + bugraaa_trade listeleri (AJAN_GOREVI.md)
        │  her yenilemede yeni hisseler sistem/main.py'ye izleme için verilir
        ▼
Sinyal motoru (sistem/engine.py, Pine ile aynı kurallar)
        │  her AL / SAT / STOP / SÜRE / GÜN SONU
        ▼
Emir yöneticisi (yazılacak: sistem/trader.py)  ──►  Alpaca paper
        │
        ▼
Telegram (her sinyalde "ALINDI" / "ATLANDI: neden") + sinyaller.csv + gün sonu özeti
```

Emir yöneticisi `sistem/main.py` içindeki `_emit()` fonksiyonuna bağlanır;
her sinyal oradan geçer.

## Pozisyon kuralları

| Kural | Değer |
|---|---|
| Hesap | Alpaca **paper** (`TradingClient(key, secret, paper=True)`) |
| Aynı anda en fazla pozisyon | **15** |
| İşlem başı tutar | **500 $** (adet = 500 ÷ fiyat, aşağı yuvarla, tam adet) |
| Gereken sermaye | 15 × 500 = 7.500 $ |
| Aynı hisseye aynı gün ikinci giriş | yok |
| 1 $ altı hisseler | **dahil** |
| Premarket girişi | var, **limit emir**, `extended_hours=True` |
| Normal seans girişi | limit emir (son fiyat + %0,5) |
| Stop | sinyaldeki stop fiyatı, girişle birlikte borsaya gönderilir |
| Çıkış | SAT / STOP / SÜRE / GÜN SONU sinyali gelince pozisyonu kapat |
| Gün sonu güvenliği | 15:55 NY'de açık kalan her şey kapatılır |

## 15 pozisyon dolduysa

Yeni AL gelir ve 15 pozisyon doluysa: **ATLA** ve Telegram'a "ATLANDI: 15 pozisyon dolu" yaz.
(Daha zayıf pozisyonu kapatıp yeniye geçmek yok — ilk sürümde basit tut.)

Aynı dakikada birden fazla AL gelirse sıralama: önce **göreceli hacmi yüksek** olan.

## 1 $ altı hisseler için ek kurallar

- Fiyat adımı 0,0001 $; limit fiyatı 4 haneye yuvarla.
- Alış-satış farkı (spread) **%2'den büyükse** girme → "ATLANDI: spread %X".
  (snapshot'tan `latest_quote.ask_price` ve `bid_price` ile hesapla)
- Alpaca 1 $ altı hisselerde kesirli (fractional) emir kabul etmez → tam adet kullan.
- Premarket'te bu hisselerde işlem çok az olabilir; limit emir 2 dakika içinde dolmazsa iptal et
  → "ATLANDI: emir dolmadı".

## Takip (her sinyali görmek için)

Her sinyal Telegram'a düşer ve yanında ne yapıldığı yazar:

```
🟢 AL  ONEG  0.91$   → ✅ ALINDI 549 adet (stop 0.84)
🟢 AL  XYZ   3.20$   → ⏭ ATLANDI: 15 pozisyon dolu
🔴 SAT ONEG  1.38$   → ✅ KAPATILDI  +%51,6  (+258 $)
```

`sinyaller.csv`'ye ek sütunlar: `islem` (ALINDI/ATLANDI/KAPATILDI), `atlama_nedeni`,
`emir_id`, `gerceklesen_fiyat`, `adet`, `kar_zarar_usd`.

Gün sonu özeti (16:05 NY): kaç sinyal, kaçı alındı, kaçı neden atlandı, toplam kâr/zarar,
**atlanan sinyaller alınsaydı ne olurdu** (kuralları veriyle düzeltmek için).

## .env'e eklenecek ayarlar

```
AUTO_TRADE=false          # true yapınca paper hesapta emir verir
MAX_POSITIONS=15
POSITION_USD=500
MAX_SPREAD_PCT=2
ORDER_TIMEOUT_SEC=120
```

## Uyarı

Testlerde premarket girişleri ve çok ucuz/oynak hisseler ortalamada zarar yazdı (README).
ONEG gibi büyük kazançlar bu grupta, ama kayıplar da. Gerçek paraya geçmeden önce en az
2–3 hafta paper sonuçlarını `sinyaller.csv`'den değerlendir.
