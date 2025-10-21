# C0dex Çoklu Timeframe Analiz Paketi

Bu depo; **app48**, **app72**, **app80**, **app96**, **app120** ve **app321** olmak üzere altı farklı timeframe uygulamasını ve ortak araçları bir araya getirir. Her uygulama CSV verisini okur, zaman damgalarını normalize eder, distorted candle (DC) işaretler, sequence ve offset hizalamalarını kurar ve hem CLI hem de hafif web arayüzleri üzerinden IOU/IOV signal scan sonuçlarını sunar.

Destekleyici paketler: `appsuite` tüm uygulamaları tek host altında bir reverse proxy ile sunar, `landing` basit bir giriş sayfası sağlar, `calendar_md` Markdown ekonomik takvimlerini JSON’a çevirir, `favicon` ortak varlıkları barındırır. Amaç; bu repo dışına çıkmadan sistemi anlamak ve çalıştırmak için gereken her şeyi tek yerde toplamaktır.

---

## İçindekiler

1. [Technology Stack](#technology-stack)
2. [Dizin Yapısı](#dizin-yapısı)
3. [Uygulama Özeti](#uygulama-özeti)
4. [Veri Akışı ve Ortak Kurallar](#veri-akışı-ve-ortak-kurallar)
5. [Distorted Candle (DC) İstisnaları](#distorted-candle-dc-İstisnaları)
6. [IOU Kısıtları](#iou-kısıtları)
7. [IOV / IOU Sinyal Motoru](#iov--iou-sinyal-motoru)
8. [Haber Entegrasyonu](#haber-entegrasyonu)
9. [CLI ve Web Kullanımı](#cli-ve-web-kullanımı)
10. [Örnek Veri Setleri](#örnek-veri-setleri)
11. [Dağıtım Notları](#dağıtım-notları)
12. [Geliştirici İpuçları](#geliştirici-ipuçları)

---

## Technology Stack

- Python 3.11 (`.python-version` Render dağıtımı için runtime’ı kilitler)
- Standard library ağırlıklı; web katmanları `http.server` tabanlı minimal HTTP servisleridir
- Production’da `gunicorn` (bkz. `requirements.txt`)
- pandas/numpy yok; veri işleme custom yardımcı fonksiyonlarla yapılır

---

## Dizin Yapısı

```
app48/      48 dakikalık analiz paketi (CLI + web)
app72/      72 dakikalık analiz paketi
app80/      80 dakikalık analiz paketi
app96/      96 dakikalık analiz paketi
app120/     120 dakikalık analiz paketi
app321/     60 dakikalık analiz paketi
appsuite/   Reverse proxy ve birleşik arayüz
landing/    Basit landing page
calendar_md/Markdown → JSON economic calendar converter (CLI + web)
economic_calendar/ IOU sayfalarının tükettiği örnek JSON takvimler
favicon/    Ortak favicon + manifest varlıkları
ornek/      Manuel eklenmiş CSV örnekleri (test)
```

Her timeframe klasörü benzer bir kalıbı izler:

- `counter.py` veya `main.py`: CLI sayaçları, converter’lar ve tahmin yardımcıları
- `web.py`: Minimal HTTP server (HTML formlar, multi-file upload, sonuç tabloları)
- `__init__.py`: Paket bildirimi ve ortak yardımcılar

---

## Uygulama Özeti

| Uygulama | Timeframe | Port | Converter | Öne Çıkan Kurallar |
|----------|-----------|------|-----------|--------------------|
| app48    | 48 dk     | 2020 | 12→48     | Synthetic 18:00 & 18:48; 18:00/18:48/19:36 DC & IOU dışında |
| app72    | 72 dk     | 2172 | 12→72     | 18:00, 19:12, 20:24 DC olamaz; ilk haftanın Cuma 16:48 IOU dışında |
| app80    | 80 dk     | 2180 | 20→80     | 18:00, 19:20, 20:40 ve tüm Cuma 16:40 DC & IOU dışında |
| app96    | 96 dk     | 2196 | 12→96     | Genel kural: 18:00 DC/IOU dışında; özel istisnalar ileride tanımlanacak |
| app120   | 120 dk    | 2120 | 60→120    | 18:00 DC & IOU dışında; Pazar hariç 20:00 ve tüm Cuma 16:00 hariç |
| app321   | 60 dk     | 2019 | —         | Pazar dışı 20:00 DC olamaz; 18:00/19:00/20:00 IOU dışında |
| appsuite | —         | 2100 | —         | Tüm uygulamalar reverse proxy arkasında |
| landing  | —         | 2000 | —         | Kartlar ve hızlı linkler |

Tüm web arayüzleri multi-file CSV upload destekler; IOU/IOV sekmeleri ve dosya kartlarında news etiketleri bulunur.

---

## Veri Akışı ve Ortak Kurallar

1. **CSV okuma:** `Time`, `Open`, `High`, `Low`, `Close (Last)` başlıkları (eş anlamlılar desteklenir) normalize edilir; bozuk satırlar atılır, veri timestamp’e göre sıralanır.
2. **Timezone normalizasyonu:** Girdi `UTC-5` ise tüm timestamp’ler +60 dk kaydırılarak `UTC-4` bazına alınır.
3. **Synthetic candles:** app48 her gün (ilk gün hariç) 18:00 ve 18:48 synthetic candle ekler; kapanış penceresi korunur.
4. **DC hesaplama:** `compute_dc_flags` DC’leri işaretler; ardışık DC engellenir. Pozitif offset adımları non-DC candle’a kaydırılır.
5. **Sequence hizalama:** `S1` (1,3,7,...) ve `S2` (1,5,9,...) desteklenir. Container rule gerektiğinde DC zaman damgasını kullanır.
6. **OC / PrevOC:** `OC = Close - Open`; `PrevOC` önceki candle’ın OC değeridir. Tahmini satırlarda `-` gösterilir.
7. **Offset sistemi:** İlk 18:00 candle baz alınır; offset aralığı `-3..+3`. Eksik veride `pred` saatleri hesaplanır.

---

## Distorted Candle (DC) İstisnaları

- Temel kural: `High ≤ prev.High`, `Low ≥ prev.Low` ve `Close` önceki candle’ın `[Open, Close]` aralığındaysa candle DC sayılır; ardışık DC engellenir.
- 18:00 baz candle DC olmaz.
- **app48:** 13:12–19:36 arası DC sayılmaz (normal kabul); synthetic 18:00 ve 18:48 candle’lar DC değildir.
- **app72:** 18:00, 19:12, 20:24 ve Cuma 16:00 DC olamaz.
- **app80:** 18:00, 19:20, 20:40 ve tüm Cuma 16:40 DC olamaz.
- **app96:** Şimdilik yalnızca genel kural uygulanır (18:00 DC değildir).
- **app120:** 18:00 ve Cuma 16:00 DC değildir; 20:00 yalnızca Pazar günleri DC değerlendirilebilir (diğer günler hariç tutulur).
- **app321:** Pazar hariç 20:00 DC olamaz; ayrıca 13:00–20:00 arası (hafta içi) DC’ler normal kabul edilir.

---

## IOU Kısıtları

Her IOU taraması aşağıdaki zamanları doğrudan hariç tutar:

- **app48:** 18:00, 18:48, 19:36
- **app72:** 18:00, 19:12, 20:24 ve iki haftalık verinin ilk haftası Cuma 16:48
- **app80:** 18:00, 19:20, 20:40 ve tüm Cuma 16:40
- **app96:** 18:00 (şimdilik genel kural)
- **app120:** 18:00, Pazar hariç 20:00 ve tüm Cuma 16:00
- **app321:** 18:00, 19:00, 20:00

IOU filtresi `limit` ve opsiyonel `± tolerance` kullanır; bir satırın sayılabilmesi için hem `|OC|` hem `|PrevOC|` değerlerinin `limit + tolerance` eşiğini aşması gerekir.

---

## IOV / IOU Sinyal Motoru

CLI ve web katmanları aynı akışı paylaşır:

1. CSV yüklenir, timezone normalize edilir, DC istisnaları uygulanır.
2. İlk 18:00 baz candle bulunur; pozitif offset’ler gerekirse bir sonraki non-DC candle’a kaydırılır.
3. Sequence hücreleri container rule ile (gerekirse DC zamanında) hizalanır.
4. `OC` ve `PrevOC` üzerinden filtre:
   - IOV: zıt işaretli ikililer + limit kontrolü
   - IOU: aynı işaretli ikililer + `limit + tolerance` kontrolü
5. Sonuçlar offset bazlı kartlarda listelenir; `syn/real` ve `(rule)` etiketleri görünür.
6. XYZ filtresi açıkken yalnızca haberli (veya korunmuş slotlu) offset’ler kalır.

Limitler pozitif varsayılır; negatif girişlerde mutlak değer alınır. `limit=0` ise sadece `OC ≠ 0` satırlar geçer.

---

## Haber Entegrasyonu

Tüm IOU sekmeleri `economic_calendar/` altındaki JSON takvimleri `news_loader.py` ile tüketir:

- **Schema:** `date`, `time`/`time_24h`, `title`, `currency`, `impact`, `all_day`, `recent_null`, `actual`, `forecast`, `previous` alanları desteklenir (eksikler kademeli doldurulur).
- **Kategori eşlemesi:**
  - `holiday`: başlıkta “holiday” geçen, all-day ve `actual=null` olan kayıtlar
  - `all-day`: tatil olmayan tüm gün etkinlikleri (örn. OPEC, German Prelim CPI)
  - `speech`: saati olan ve `actual=null` olan konuşmalar
  - `normal`: standart veri açıklamaları
- `holiday` ve `all-day` sadece bilgilendirme amaçlıdır; XYZ elemesini tetiklemez.
- `recent_null=true` olan kayıtlar `(null)` ekiyle gösterilir.
- app72’de 16:48/18:00/19:12/20:24 özel slotları, haber olmasa da korunur.

XYZ filtresi, haber bulunmayan ve slotla korunmayan offset’leri eler; holiday / all-day satırlar kalır ama bilgi etiketiyle gösterilir.

---

## CLI ve Web Kullanımı

Optional virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Web Arayüzleri

```bash
python3 -m landing.web      --host 0.0.0.0 --port 2000
python3 -m appsuite.web     --host 0.0.0.0 --port 2100
python3 -m app48.web        --host 0.0.0.0 --port 2020
python3 -m app72.web        --host 0.0.0.0 --port 2172
python3 -m app80.web        --host 0.0.0.0 --port 2180
python3 -m app96.web        --host 0.0.0.0 --port 2196
python3 -m app120.web       --host 0.0.0.0 --port 2120
python3 -m app321.web       --host 0.0.0.0 --port 2019
python3 -m calendar_md.web  --host 0.0.0.0 --port 2300
```

Tüm arayüzler multi-file upload, IOU `limit/tolerance` alanları ve opsiyonel XYZ filtresi sunar; uygun yerlerde CSV indirme seçenekleri bulunur.

### CLI Örnekleri

```bash
# app120 analysis using sequence S2, +1 offset, and DC visibility
python3 -m app120.counter --csv data.csv --sequence S2 --offset +1 --show-dc

# 60 → 120 minute converter
python3 -m app120 --csv 60m.csv --input-tz UTC-5 --output 120m.csv

# app80 IOU scan with custom thresholds
python3 -m app80.counter --csv data.csv --sequence S1 --scan-iou --limit 0.08 --tolerance 0.005

# app96 temel IOU taraması
python3 -m app96.counter --csv data.csv --sequence S2 --offset 0 --show-dc

# app48 prediction mode
python3 -m app48.main --csv data.csv --predict 49

# Markdown economic calendar → JSON
python3 -m calendar_md --input calendar.md --output economic_calendar/calendar.json --year 2025
```

Tüm CLI araçlarında `--help` ile detaylı argüman listesini görebilirsiniz.

---

## Örnek Veri Setleri

Depoda otomatik test verileri yoktur. Bunun yerine `ornek/` klasöründe her timeframe için gerçek-dünya senaryolarını temsil eden CSV’ler bulunur. IOU/IOV davranışı, news kategorileri ve tolerance etkisini doğrulamak için kullanabilirsiniz; kendi dosyalarınızı da buraya ekleyebilirsiniz.

---

## Dağıtım Notları

- `render.yaml` ve `Procfile` Render barındırma komutlarını örnekler
- `railway.toml` Railway/Nixpacks varsayılanlarını içerir
- `Dockerfile` minimal bir Python imajıyla web servislerini başlatır
- Production’da her servis için farklı port tanımlayın; tek entrypoint için `appsuite` reverse proxy önerilir

---

## Geliştirici İpuçları

- Hızlı sözdizimi kontrolü için `python3 -m compileall .`; unittest/pytest entegrasyonu yok
- IOU sekmelerindeki multi-upload, yeni veri veya tolerance değişikliklerini hızlıca doğrulamak için idealdir
- Yeni timeframe eklerken referans olarak `app120`’yi alın; DC/IOU yardımcılarını yeniden kullanın
- `__pycache__` klasörlerini sürüme almayın
- Takvim JSON’larını güncel tutun; `speech` ve `all-day` kategorileri sadece bilgilendirir, XYZ elemesini etkilemez

Hızlı bir başlangıç için `landing.web` veya `appsuite.web`’i açın, `ornek/` altındaki dosyaları yükleyin ve IOU kartlarını inceleyin. Böylece DC istisnaları, news etiketleri ve tolerance eşikleri pratikte görülebilir.
