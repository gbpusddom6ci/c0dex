# AGENTS.md — Proje Rehberi (Geliştirici + Yapay Zekâ Ajanı)

Bu dosya, repodaki bütün uygulamaların ve ortak altyapının gerçeğe uygun, tek kaynaktan anlaşılmasını sağlar. Amaç, yeni devralan geliştiricilerin ve yapay zekâ ajanlarının başka hiçbir kaynağa ihtiyaç duymadan projeye hâkim olabilmesidir.


## 1) Hızlı Bakış

- Zaman dilimi: Tüm çıktı UTC-4. Girdi UTC-5 seçilirse +1 saat kaydırılır (normalize edilir).
- Dil/çatı: Python 3.11+. Standart kütüphane ağırlıklı; web katmanları `http.server` tabanlı. Üretim için yalnız `gunicorn` (requirements.txt).
- Uygulamalar: app48, app72, app80, app90, app96, app120, app321 (her biri CLI + web). Ek: appsuite (reverse proxy), landing (tanıtım), calendar_md (takvim dönüştürücü), favicon (varlıklar), news_loader (haber motoru).
- IOU/IOV sinyalleri: Dizi (S1/S2), offset hizalama, DC istisnaları ve limit/tolerans eşikleriyle üretilir. “XYZ (haber filtreli)” opsiyonu vardır.
- Örüntüleme (Pattern): Tüm IOU sayfalarında Joker desteği, tooltip ve beam=512; en fazla 1000 örüntü listelenir.
- Stacked analysis: IOU sonuçları aynı sayfada birikmeli tutulur (app120’de yalnız IOU). 


## 2) Dizin Yapısı ve Bileşenler

- `app48/`, `app72/`, `app80/`, `app90/`, `app96/`, `app120/`, `app321/` — Timeframe uygulamaları (CLI + web)
- `appsuite/` — Tüm web uygulamalarını tek host altında farklı path’lerle proxy’ler
- `landing/` — Basit landing sayfası (uygulama linkleri)
- `calendar_md/` — ForexFactory benzeri markdown takvimlerini JSON’a dönüştürür (CLI + web)
- `economic_calendar/` — Örnek takvim JSON dosyaları (haber entegrasyonu için)
- `favicon/` — Favicon ve manifest varlıkları + ortak `<head>` linkleri
- Kök: `Dockerfile`, `Procfile`, `render.yaml`, `railway.toml`, `.python-version`


## 3) Ortak Kavramlar ve Kurallar

### 3.1 CSV Girdisi
- Gerekli başlıklar: `Time`, `Open`, `High`, `Low`, `Close (Last)` (eş anlamlı başlık adları kodda desteklenir).
- Satırlar timestamp’e göre sıralanır; bozuk satırlar atlanır.
- Girdi TZ: `UTC-4` veya `UTC-5`. `UTC-5` seçilirse tüm zaman damgaları +60 dk kaydırılır ve çıktı UTC-4’e normalize edilir.

### 3.2 Diziler (Sequences)
- S1: `1, 3, 7, 13, 21, 31, 43, 57, 73, 91, 111, 133, 157`
- S2: `1, 5, 9, 17, 25, 37, 49, 65, 81, 101, 121, 145, 169`

### 3.3 Distorted Candle (DC)
- Tanım: `High ≤ prev.High`, `Low ≥ prev.Low` ve `Close` önceki mumun `[Open, Close]` aralığında ise DC kabul edilir.
- Ardışık DC engeli: Aynı anda iki DC olamaz (ardışık DC false’a çekilir).
- Varsayılan: 18:00 mumu asla DC sayılmaz (uygulama istisnaları ayrıca geçerli).

DC İstisnaları (uygulamaya özel):
- app321 (60m): 13:00–20:00 arası DC’ler normal kabul; 20:00 (Pazar hariç) DC olamaz.
- app48 (48m): 13:12–19:36 (19:36 dahil) arası DC’ler normal kabul edilir.
- app72 (72m): 18:00; Cuma 16:48; (Pazar hariç) 19:12, 20:24; Cuma 16:00 DC olamaz.
- app80 (80m): (Pazar hariç) 18:00, 19:20, 20:40; Cuma 16:40 DC olamaz.
- app90 (90m): 18:00; (Pazar hariç) 19:30; Cuma 16:30 DC olamaz.
- app96 (96m): 18:00; (Pazar hariç) 19:36; Cuma 16:24 DC olamaz.
- app120 (120m): 18:00 DC değildir; (Pazar hariç) 20:00 DC olamaz; Cuma 16:00 DC sayılmaz (hafta kapanışı).

Kapsayıcı Kural: Dizi hücresi bir DC’ye denk gelirse zaman damgası o DC mumuna yazılır.

Pozitif Offset DC İstisnası: +1, +2, +3 başlangıç adımlarında başlangıç mumu DC ise “normal mum” gibi sayılır (pozitif offsette DC kısıtı uygulanmaz).

### 3.4 Offset Sistemi ve Hizalama
- Başlangıç: İlk yakalanan 18:00 mumu.
- Offset aralığı: `-3..+3`, hedef zaman = offset × timeframe dakikası.
- Pozitif offsetlerde “DC olmayan sonraki mum” mantığı ile kaydırma yapılır; veri yoksa `missing_steps` raporlanır ve tahmini zaman (`pred`) üretilir.
- Matrix görünümlerinde tüm offset sütunları tek tabloda listelenir.

Pozitif vs Negatif Offsetlerde DC Kaydırma:
- Pozitif (+1/+2/+3): Başlangıç noktası seçilirken DC mumlar atlanır ve ilk “DC olmayan gerçek” muma kaydırılır. Böylece +offset sütunları DC’ye kilitlenmez ve çakışmalar azalır (ör. 20:00 DC ise +1 başlangıcı 22:00; 22:00 da DC ise 00:00).
- Negatif (-1/-2/-3): Hedef zaman doğrudan kullanılır (ör. -1 → 16:00). Hedef mum DC olsa bile geri doğru “bir sonraki DC olmayan mumu bul” araması yapılmaz; 16:00 DC ise başlangıç yine 16:00 kabul edilir.
- Kapsam notu: “Kapsayıcı kural” her iki yönde de geçerlidir; dizi adımı DC’ye denk gelirse o DC’nin timestamp’i yazılır. 0 offset zaten 18:00’dır ve uygulama kuralları gereği DC sayılmadığından ek işleme gerek yoktur.

Hafta Sonu Kapanış/Açılış (tahmin):
- app72, app80, app120: Cuma kapanışından sonra tahminler Pazar 18:00’a atlar.
- app48, app90, app96, app321: Doğrusal adımla dakika eklenir (özel hafta sonu sıçraması yok).

### 3.5 OC / PrevOC
- OC: `Close - Open` (her gerçek mum için `±5` hane ile gösterilir).
- PrevOC: Bir önceki mumun OC değeri; yoksa `-`.
- Tahmini satırlarda `OC=- PrevOC=-` yazılır.

### 3.6 IOU/IOV Eşik Mantığı
- Limit mutlak değerdir; negatif girilirse `abs(limit)` alınır.
- IOU: Etkin eşik `limit + tolerans` ve karşılaştırma “≥”. Kodda `abs(x) < (limit+tolerans)` ise eleme yapılır.
- IOV: Tolerans 0 kabul edilir (yalnız limit uygulanır).
- Limit=0 davranışı:
  - IOU: Eşik = `tolerans` (varsayılan 0.005). Her iki mutlak değer de bu eşiği aşmalıdır.
  - IOV: Eşik = 0; işaret koşulu sıfır OC’ları pratikte dışarıda bırakır.
- Dizi “skip” kuralı (IOU/IOV için ortak): S1’de `1` ve `3`; S2’de `1` ve `5` sinyal dışıdır.

### 3.7 IOU Zaman Kısıtları (Sinyal Olamayan Saatler)
- app321: 18:00, 19:00, 20:00 IOU olamaz.
- app48: 18:00, 18:48, 19:36 IOU olamaz.
- app72: 15:36 ve 16:48 mumları her gün IOU değildir; 18:00, 19:12, 20:24 kısıtı ise “ikinci Pazar” gününde serbesttir. İlk haftanın Cuma 16:48 mumu ayrıca IOU dışıdır.
- app80: 15:20, 16:40 ve 18:00 IOU değildir; 19:20 ve 20:40 yalnız Pazar günleri serbesttir; Cuma 16:40 ayrıca IOU dışıdır.
- app90: 15:00, 16:40 ve 18:00 IOU değildir; (Pazar hariç) 19:30 ve Cuma 16:30 da IOU vermez.
- app96: 14:48, 16:24 ve 18:00 IOU değildir; (Pazar hariç) 19:36 kısıtı sürer; Cuma 16:24 zaten IOU dışıdır.
- app120: 16:00 ve 18:00 IOU değildir; 20:00 tüm günlerde IOU vermez (Pazar dahil).

### 3.8 XYZ (Haber Filtreli) Kümesi
- IOU formlarında “XYZ kümesi (haber filtreli)” seçeneği ile, etkili haberi olmayan hit’lerin offsetleri elenir.
- Haber kaynağı: `news_loader.py` JSON’ları yükler; `time_24h` yoksa `time`/`time_text`/`time_label`/`session` denenir. Kayıtlar kategorize edilir:
  - `holiday` (tatil), `all-day` (gün boyu), `speech` (saati belli olup değerleri null), `normal`.
- Hücre etiketi: `Var`, `Holiday`, `AllDay` veya `Yok`. `Holiday` ve bilgi amaçlı satırlar haber sayılmaz.
- “Recent-null” penceresi: Son `null_back_minutes=60` dakikada “actual=null” olan kayıtlar da listelenir.
- app72 özel slot koruması (XYZ): Haber listesi boşsa 16:48, 18:00, 19:12, 20:24 slotları “Kural slot HH:MM” notuyla korunur; tatil/all-day gibi bilgi satırları geldiğinde bu koruma devreden çıkar.
- Eşik entegrasyonu: XYZ hesabında da `|OC|` ve `|PrevOC| ≥ (limit + tolerans)` koşulu aranır.

Özet tablo (yalnız XYZ kümesi):
- IOU formlarındaki “Özet tablo” seçeneği açıkken ayrıntılı hit tabloları yerine dosya başına yalnız XYZ kümesi ve elenen offsetlerin nedenleri özet bir tabloda gösterilir. Örüntüleme açıksa, bu modda da örüntü paneli eklenmeye devam eder.

Not (spesifikasyon ↔ web farkı): Web IOU sayfalarında XYZ elemesi şu an “OR” ve “>” ile çalışır (haber yoksa ve `|OC| > eşiği` veya `|PrevOC| > eşiği` ise offset elenir). Teorik çekirdek kural “AND” ve “≥”dir. Bu fark bilinçli şekilde belgelenmiştir.

### 3.9 Örüntüleme (Pattern) ve Joker
- Tüm IOU sayfalarında “Örüntüleme” seçeneği ile dosya bazlı XYZ kümelerinden kurallı diziler üretilir.
- Kurallar:
  - Başlangıç serbest: 0 veya ±1/±2/±3. Her 0’dan sonra yeni üçlü yalnız ±1 veya ±3 ile başlar.
  - Üçlü: Aynı işaretle 1–2–3 (yükselen) veya 3–2–1 (azalan) tamamlanır; üçlü bitmeden 0 alınamaz.
  - İlk adım ±1/±3 ise ikinci adımda 0’a izin verilir (otomatik).
  - ±2 ile başlanırsa ikinci adım aynı işaretin 1 veya 3’ü olmak zorunda.
  - Arka arkaya aynı değer olamaz; işaret üçlü içinde sabittir; tüm dosyalar kronolojide tüketilir (atlama yok).
- Joker: Joker işaretli dosyanın XYZ kümesi tüm offsetleri (-3..+3) kapsar.
- Görsellik: Her offset üstüne gelince kaynak dosya adı tooltip olarak görünür; Joker’ler “(Joker)” etiketi alır. Aynı üçlünün (0’sız) birden fazla tekrar ettiği örüntülerde blok arka planları renklendirilir.
- Performans: beam=512; en fazla 1000 örüntü listelenir.

### 3.10 Stacked Analysis (IOU)
- Tüm IOU sayfalarında yeni analizler “Analiz #YYYYMMDD_HHMMSS” başlığıyla üstte birikir; form sayfa altında yeniden render edilir.
- Önceki sonuçlar `previous_results_html` alanı ile base64 olarak korunur ve Joker seçiminde “Önceki Analizler” bölümü gösterilir.
- Not: app120’de stacked analysis yalnız IOU için geçerlidir (IOV klasik tek sonuçtur).


## 4) Uygulama Başlıkları

Her uygulama tipik olarak şu modüllere sahiptir: `counter.py` (sayım + sinyal), `main.py` (converter), `web.py` (minimal HTTP arayüzü).

### 4.1 app48 (48m)
- Port: 2020. Sekmeler: Analiz, DC List (filtreli), Matrix, IOU Tarama, 12→48 Converter (route: `/convert`).
- Sentetik mumlar: İlk gün hariç her gün 17:12–19:36 arasına 18:00 ve 18:48 eklenir (open/close lineer; high/low min/max). DC hesapları sentetik sonrası yeniden yapılır.
- DC istisnası: 13:12–19:36 arası DC’ler normal kabul. IOU’da 18:00/18:48/19:36 asla listelenmez.
- IOU: Limit + ±tolerans (vars 0.005), çoklu CSV, XYZ ve örüntüleme destekli. Eşik “≥”.
- DC List: `only_syn` / `only_real` filtreleri vardır.

### 4.2 app72 (72m)
- Port: 2172. Sekmeler: Analiz, DC List, Matrix, IOU Tarama, 12→72 Converter.
- DC istisnaları: 18:00; Cuma 16:48; (Pazar hariç) 19:12, 20:24; Cuma 16:00 DC olamaz.
- IOU kısıtları: 15:36 ve 16:48 mumları günlük olarak IOU dışıdır; 18:00/19:12/20:24 ise “ikinci Pazar” gününde serbest kalır. İlk haftanın Cuma 16:48 IOU değildir.
- IOU: Limit + ±tolerans (≥); çoklu CSV; XYZ; örüntüleme + Joker. Stacked analysis açık.
- 12→72: 7×12m → 1×72m; Pazar 18:00 öncesi ve Cumartesi atlanır. Tahmin motoru hafta sonu boşluğunu atlar.

### 4.3 app80 (80m)
- Port: 2180. Sekmeler: Analiz, DC List, Matrix, IOU Tarama, 20→80 Converter.
- DC/IOU kısıtları: (Pazar hariç) 18:00, 19:20, 20:40 DC olamaz; Cuma 16:40 kapanış. IOU’da 15:20, 16:40, 18:00 her gün dışlanır; 19:20/20:40 yalnızca Pazar günleri serbesttir.
- IOU: Limit + ±tolerans (≥), çoklu CSV, XYZ, örüntüleme + Joker, stacked.
- 20→80: 4×20m → 1×80m (open=ilk, close=son, high/low blok max/min). Tahminde hafta sonu atlanır.

### 4.4 app90 (90m)
- Port: 2190. Sekmeler: Analiz, DC List, Matrix, IOU Tarama, 30→90 Converter.
- DC/IOU kısıtları: 18:00; (Pazar hariç) 19:30; Cuma 16:30. IOU’da ek olarak 15:00 ve 16:40 her gün dışlanır.
- IOU: Limit + ±tolerans (≥), çoklu CSV, XYZ, örüntüleme + Joker, stacked.
- 30→90: 3×30m → 1×90m; Cumartesi ve Pazar 18:00 öncesi atlanır.

### 4.5 app96 (96m)
- Port: 2196. Sekmeler: Analiz, DC List, Matrix, IOU Tarama, 12→96 Converter.
- DC/IOU kısıtları: 18:00; (Pazar hariç) 19:36; Cuma 16:24. IOU’da ek olarak 14:48 ve 16:24 her gün dışlanır.
- IOU: Limit + ±tolerans (≥), çoklu CSV, XYZ, örüntüleme + Joker, stacked.
- 12→96: 8×12m → 1×96m; Cumartesi ve Pazar 18:00 öncesi atlanır.

### 4.6 app120 (120m)
- Port: 2120. Sekmeler: Analiz, DC List, Matrix, IOV Tarama, IOU Tarama, 60→120 Converter.
- DC istisnaları: 18:00 DC değildir; (Pazar hariç) 20:00 DC olamaz; Cuma 16:00 DC sayılmaz.
- IOU kısıtları: 16:00 ve 18:00 her gün dışlanır; 20:00 tüm günlerde IOU değildir (Pazar dahil).
- IOV: Zıt işaretli, eşik üstü çiftler. IOU: Aynı işaretli, `limit + tolerans` ≥ eşik. Limit negatifse abs alınır.
- IOU: Çoklu CSV, XYZ, örüntüleme + Joker, stacked (IOV klasik).
- 60→120: Normalize eder, CSV indirilebilir.

### 4.7 app321 (60m)
- Port: 2019. Sekmeler: Analiz, DC List, Matrix, IOU Tarama.
- DC istisnaları: 13:00–20:00 arası DC’ler normal kabul; 20:00 (Pazar hariç) DC olamaz.
- IOU kısıtları: 18:00, 19:00, 20:00 IOU değildir.
- IOU: Limit + ±tolerans (≥), çoklu CSV, XYZ, örüntüleme + Joker, stacked.


## 5) Web Katmanı ve Ortak Araçlar

### 5.1 landing
- `python3 -m landing.web --port 2000` basit kartlı sayfa üretir.

### 5.2 appsuite (Reverse Proxy)
- Tüm uygulamaları tek host altında path bazlı sunar (ör: `/app72`).
- Arka uçlar ayrı thread’lerde başlar; health: `/health` → `ok`.
- HTML’de `href` ve `action` yolları proxy prefix’ine göre rewrite edilir.
- Güvenlik başlıkları: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Content-Security-Policy` (inline stil izinli) gönderilir.
- Maksimum upload: 50 MB; çoklu dosya sayısı: 50.

### 5.3 calendar_md ve news_loader
- calendar_md web: `python3 -m calendar_md.web --port 2300` — çoklu `.md` yükler, her biri için JSON üretir (çoklu ise ZIP). CLI: `python3 -m calendar_md --input takvim.md --output out.json --year 2025`.
- news_loader: `economic_calendar/*.json` dosyalarını birleştirir ve önbellekler; alanlar eksikse alternatif adlar denenir. Kategoriler: `holiday`, `all-day`, `speech`, `normal`. “All Day” kayıtlar gün bazında eşleştirilir; `recent-null` penceresi desteklenir.


## 6) Dağıtım ve Çalıştırma

1) Python 3.11+ kurulu olmalı.
2) (İsteğe bağlı) `python3 -m venv .venv && source .venv/bin/activate`
3) `pip install -r requirements.txt` (yalnız `gunicorn`)
4) CLI örneği: `python3 -m app120.counter --csv data.csv`
5) Web örneği: `python3 -m app120.web --host 0.0.0.0 --port 2120`
6) Üretim: `gunicorn app120.web:main` benzeri komutlar.


## 7) Geliştirici Notları (Ajanlar için de geçerli)

- Stil: Mevcut mimariye uygun, minimal bağımlılık. Gereksiz karmaşıklıktan kaçının.
- Veri yok: Repoda hazır CSV bulunmaz; kendi veri setinizi kullanın.
- Eşik sınırları: IOU’da `≥ (limit + tolerans)`; IOV’da tolerans yok. Dizi “skip” kuralı (S1: 1,3 — S2: 1,5) tüm taramalarda geçerli.
- app48 `/convert` rotası diğerlerinden farklıdır (diğerleri `/converter`).
- XYZ filtresi spec↔web farkı (OR ve `>` vs AND ve `≥`) bilerek belgelenmiş bir sapmadır.
- Güvenlik başlıkları tüm web katmanlarında set edilir; inline CSS tooltipler için CSP’de `unsafe-inline` stil izni vardır.
- Maksimum yükleme büyüklükleri ve dosya sayıları UI’da ve appsuite içinde sınırlandırılmıştır (50MB/50).


## 8) Sorun Giderme ve İpuçları

- Limit=0 çoğu sinyali eler; IOU’da tolerans varsayılanı (0.005) nedeni ile pratik eşik budur.
- DC List sekmeleri ham DC’leri CSV’ye çıkarmak için uygundur.
- IOU sonuçlarındaki “(rule)” etiketi DC kapsaması ile eşleşir; app48’te “syn/real” ayrımı vardır.
- app72’de “ikinci Pazar” kuralı: IOU kısıtlı saatler (18:00, 19:12, 20:24) ikinci Pazar gününde serbesttir; 15:36 slotu her zaman kapalı kalır, 16:48 ise XYZ’de özel slot koruması altındadır.


— Son —
