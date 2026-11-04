# Buğra Günlük Trade Sistemi

Gün içinde **yükselen ABD hisselerini** otomatik bulur, senin
"Buğra Çoklu Hisse Swing" kurallarını **1 dk veya 5 dk** mumlarda canlı çalıştırır,
sinyali **mum kapandığı anda Telegram'a** gönderir.

```
Alpaca tarayıcı (her 60 sn)        Alpaca websocket (canlı 1 dk mum)
  en çok yükselenler +                     │
  en aktifler → %10+ olanlar ──► izleme ──►│──► 1dk → 5dk birleştirici
                                           │          │
                                           ▼          ▼
                                  Sinyal motoru (Pine'ın birebir aynısı)
                                           │
                                           ▼
                                Telegram  +  sinyaller.csv
```

## Neden TradingView sitesinden daha hızlı?

TradingView alarmı da mum kapanışında tetiklenir ama sunucu → uygulama bildirimi
arasında gecikme oluşabiliyor. Bu sistemde:

- Alpaca 1 dk mumu kapanıştan ~1–2 sn sonra gönderir,
- motor 30 hisseyi ~0,5 sn'de hesaplar,
- Telegram mesajı ~0,3 sn'de telefona düşer.

Her mesajın altında **"⏱ mum kapanışından X sn sonra"** yazar, gecikmeyi kendin görürsün.
5 dk modunda 5 dk mum, içindeki son dakika geldiği anda kapanır (bir sonraki mumu beklemez).

## Kurulum (Windows)

1. **Alpaca anahtarı:** alpaca.markets → Paper hesabı → API Keys → Key + Secret.
2. **Telegram botu:**
   - Telegram'da `@BotFather` → `/newbot` → isim ver → **token**'ı kopyala.
   - Botuna bir mesaj at, sonra tarayıcıda
     `https://api.telegram.org/bot<TOKEN>/getUpdates` aç → `"chat":{"id": ...}` sayısı **chat id**.
3. `baslat.bat`'a çift tıkla. İlk seferde `.env` açılır, anahtarları yaz, kaydet, tekrar çalıştır.

Bilgisayar açık kaldığı sürece çalışır. Premarket 11:00 TR'de (kış saatinde 12:00) başlar.

## Yeni bilgisayarda kurulum (GitHub yedeğinden)

1. **Python 3.11+** kur (python.org, "Add to PATH" işaretli) ve **Git** kur.
2. `cd Desktop` → `git clone <depo adresi> tradeeeee`
3. `.env` dosyasını klasöre koy (yedeğin yoksa `.env.example`'ı `.env` adıyla kopyala, Alpaca + Telegram anahtarlarını yaz;
   chat id için yukarıdaki Telegram adımı).
4. `baslat.bat`'a bir kez çift tıkla → `.venv` ve paketler kendiliğinden kurulur. Kontrol: `.venv\Scripts\python -m pytest -q tests`
5. **TradingView Desktop** kur, hesabına gir, `--remote-debugging-port=9222` ile başlat (TradingView MCP `tv_launch` bunu yapar).
   Pine scriptleri, alarmlar ve listeler TradingView hesabında durur; `pine/` klasöründe de kopyaları var.
6. PowerShell'de `.\gorev_kur.ps1` → hafta içi 11:55 TR'de `liste_yenile.bat` (takipçi + site + liste yenileyici) başlar.
7. Backtest verisi GitHub'da yok (7 GB): gerekirse `backtest/veri_tam.py` ile Alpaca'dan yeniden indir.

## Ayarlar (`.env`)

| Ayar | Varsayılan | Açıklama |
|---|---|---|
| `TIMEFRAME_MIN` | 1 | 1 veya 5 dk |
| `MACD_FILTER` | true | AL için MACD (mavi) > sinyal (turuncu) |
| `PREMARKET_ENTRY` | false | Premarket'te AL verilsin mi |
| `MIN_GAIN_PCT` | 5 | Önceki kapanışa göre en az % artış |
| `MIN_PRICE` | 0.2 | Bu fiyatın altındaki hisseler atlanır (1 $ altı dahil) |
| `ALPACA_FEED` | iex | `sip` = ücretli tam piyasa verisi |

## Test sonuçları (30 Ekim 2026, TradingView strateji test motoru)

Bugünkü yükselenlerden CVNA, PRCH, GKOS, NUWE ile **normal mumlarda**, komisyon hariç,
işlem başına ortalama getiri:

| | 1 dk MACD yok | 1 dk MACD var | 5 dk MACD yok | 5 dk MACD var |
|---|---|---|---|---|
| Büyük hisseler, tüm işlemler | −0,07% | −0,08% | −0,09% | −0,10% |
| Büyük hisseler, gün ≥ %10 iken | +0,20% (32) | +0,24% (28) | +0,01% (29) | +0,15% (20) |
| Büyük hisseler, premarket girişi | −0,50% | −0,66% | −0,49% | −0,53% |
| NUWE (küçük/oynak), tüm işlemler | −1,42% | −1,46% | −1,99% | −2,08% |

Ne anlama geliyor:

- **MACD filtresi** işlem sayısını ~%8–10 azaltıyor, sonucu belirgin değiştirmiyor.
  Sadece "gün ≥ %10" grubunda biraz iyileşme var ama örnek az (20–30 işlem).
- **Premarket girişleri** her yerde zararlı → varsayılan kapalı.
- **Küçük, çok oynak hisselerde** kurallar ciddi zarar yazıyor.
- Strateji komisyon/spread öncesinde **başabaş civarında**. Gerçek parayla kullanmadan önce
  en az birkaç hafta sinyalleri `sinyaller.csv`'de takip et.
- ⚠ TradingView grafiğin **Heikin Ashi** mumda. Heikin Ashi'de strateji testi sahte (ortalama)
  fiyatlarla yapılır ve sonuçları olduğundan iyi gösterir — test için normal mum kullan.

Ham sonuçlar: `backtest/sonuclar_normal_mum.jsonl` (geçerli). `heikin_ashi_*_GECERSIZ` dosyaları ilk denemedir, karşılaştırma için duruyor.

## Doğruluk kontrolü

`tests/compare_tv.py` Python motorunu TradingView'daki Pine stratejisinin işlemleriyle
karşılaştırır (TradingView Desktop açık, grafik **normal mum**, strateji varsayılan ayarlarda):

```
.venv\Scripts\python -m tests.compare_tv            # grafikteki sembol + zaman dilimi
.venv\Scripts\python -m pytest tests -q             # birim testleri
```

31 Ekim 2026 kontrolü (her AL / SAT / STOP / SÜRE / GÜN SONU aynı mumda, aynı fiyatla):

| Sembol | Zaman dilimi | Mum | Sonuç |
|---|---|---|---|
| GDYN | 1 dk | 20.222 | **894 / 894** olay aynı |
| ONEG (1 $ altı) | 1 dk | 19.910 | **814 / 814** olay aynı |
| CVNA | 5 dk | 20.043 | **641 / 641** olay aynı (ilk gün ısınma hariç) |
| CVNA | 1 dk → 5 dk birleştirici | 20.300 | **142 / 142** olay aynı · 4.810 / 4.810 mum OHLC aynı |

Bu kontrolde bulunup düzeltilen 3 ince nokta (kodda açıklamalı):
- **DMI:** yükseliş ve düşüş eşitken (6,03−6,02 ile 6,02−6,01) kayan nokta gürültüsü +DM yazıyordu.
- **Bollinger:** kapanış bant çizgisine tam eşit olduğunda kararın TradingView'la aynı çıkması için
  ortalama/sapma TradingView'daki gibi kayan toplamla hesaplanıyor.
- **Yarım günler** (Şükran Günü ertesi, 24 Aralık): seans 13:00'te biter, GÜN SONU 12:59 mumunda.
  Canlı sistem yarım günleri açılışta Alpaca borsa takviminden alır.

## Dosyalar

- `sistem/indicators.py` — Pine `ta.*` fonksiyonlarının birebir karşılıkları
- `sistem/engine.py` — sinyal kuralları + 1dk→5dk birleştirici
- `sistem/scanner.py` — yükselen hisse tarayıcı (sadece NASDAQ/NYSE/AMEX hisseleri, ETF/ETN hariç —
  filtreler/*.json ile aynı)
- `sistem/telegram.py` — Telegram bildirimi (arka planda, akışı bekletmez)
- `sistem/main.py` — hepsini çalıştıran program
- `pine/coklu_swing_intraday_macd.pine` — TradingView'a eklenen yeni script
- `filtreler/premarket.json` — tüm ABD borsası premarket taraması (TradingView'da: **bugraaa_premarket**)
- `filtreler/piyasa.json` — tüm ABD borsası piyasa saati taraması (TradingView'da: **bugraaa_trade**)
- `filtreler/AJAN_GOREVI.md` — listeleri izleme listesine yazıp yeni girenleri bildirecek ajan görevi
- `filtreler/OTOMATIK_ISLEM.md` — Alpaca paper ile otomatik işlem kuralları (15 pozisyon × 500 $, taslak)
