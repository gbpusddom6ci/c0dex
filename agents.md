# AGENTS.md — Proje Rehberi (Geliştirici + Yapay Zekâ Ajanı)

Bu dosya, repodaki tüm uygulamaların ve ortak altyapının güncel, tekil referansıdır. Amaç, yeni geliştirici veya yapay zekâ ajanlarının başka kaynağa ihtiyaç duymadan projeye hâkim olmasıdır.

## 1) Hızlı Bakış

- Zaman dilimi: Çıktı bakışı UTC-4. Web arayüzleri ve tüm dönüştürücülerde `input_tz` seçimiyle UTC-5 giriş +1 saat kaydırılır; CLI counter’lar UTC-4 kabul eder (yalnız app48 counter `--input-tz` alır).
- Dil/çatı: Python 3.11+, standart kütüphane + `http.server`. Üretimde sadece `gunicorn`.
- Uygulamalar: app48, app72, app80, app90, app96, app120, app321 (her biri CLI + web). Ek: appsuite (reverse proxy), landing (tanıtım), calendar_md (takvim dönüştürücü), favicon (varlıklar), news_loader (haber motoru).
- IOU/IOV: S1/S2 dizileri, offset hizalama, DC istisnaları, limit+tolerans mantığı, XYZ (haber) filtresi, örüntüleme + Joker, stacked sonuçlar. app120’de IOV ayrı sekme.
- Yeni/özel: app90’da OC/PrevOC toplama sekmesi (haber filtresi opsiyonel). app72 ve app120 IOU’da örüntü zinciri geçmişi korunur. Tüm dönüştürücüler çoklu dosyayı destekler, birden fazlaysa ZIP indirir.
- Upload limitleri: Web formları ve appsuite 50 MB; dosya sayısı 50 (app321 IOU ve calendar_md 25). Güvenlik başlıkları her yerde set edilir.

## 2) Dizin Yapısı ve Bileşenler

- `app48/`, `app72/`, `app80/`, `app90/`, `app96/`, `app120/`, `app321/` — Timeframe uygulamaları (CLI + web)
- `appsuite/` — Tüm web uygulamalarını tek host altında farklı path’lerle proxy’ler
- `landing/` — Landing sayfası (uygulama linkleri, görsel varlıklar için `photos/`)
- `calendar_md/` — ForexFactory tarzı markdown takvimlerini JSON’a dönüştürür (CLI + web)
- `economic_calendar/` — Haber JSON örnekleri (news_loader için; kök dizindeki JSON’lar da taranır)
- `favicon/` — Favicon ve manifest varlıkları + `<head>` linkleri
- Kök: `Dockerfile`, `Procfile`, `render.yaml`, `railway.toml`, `.python-version`, yardımcı md/csv örnekleri

## 3) Ortak Kavramlar ve Kurallar

### 3.1 Zaman Dilimi & Upload
- Web ve dönüştürücülerde `input_tz` UTC-5 seçilirse tüm timestamp’ler +1h kaydırılıp çıktıda UTC-4 normalize edilir. Counter CLI’lar (app48 hariç) verinin zaten UTC-4 olduğuna inanır.
- Maksimum yük: 50 MB. Dosya sınırı: 50; app321 IOU ve calendar_md 25. Birden fazla çıktı varsa ZIP paketlenir, dosya adları sanitize edilir ve benzersizlenir.

### 3.2 CSV Girdisi
- Gerekli başlıklar: `Time`, `Open`, `High`, `Low`, `Close (Last)` (eş anlamlı başlıklar desteklenir).
- Satırlar timestamp’e göre sıralanır; hatalı/boş satırlar atlanır.

### 3.3 Diziler (Sequences)
- S1: `1, 3, 7, 13, 21, 31, 43, 57, 73, 91, 111, 133, 157`
- S2: `1, 5, 9, 17, 25, 37, 49, 65, 81, 101, 121, 145, 169`

### 3.4 Distorted Candle (DC)
- Tanım: `High ≤ prev.High`, `Low ≥ prev.Low`, `Close` önceki mumun `[Open, Close]` aralığında ise DC kabul edilir; ardışık DC engellenir.
- Varsayılan: 18:00 DC sayılmaz; uygulama istisnaları aşağıda.
- DC istisnaları:
  - app321 (60m): 13:00–20:00 DC’ler normal kabul; 20:00 (Pazar hariç) DC olamaz.
  - app48 (48m): 13:12–19:36 arası DC’ler normal kabul.
  - app72 (72m): 18:00; Cuma 16:48; (Pazar hariç) 19:12, 20:24; Cuma 16:00 DC olamaz (hafta kapanış boşluğu kontrolü mevcut).
  - app80 (80m): (Pazar hariç) 18:00, 19:20, 20:40 DC olamaz; Cuma 16:40 DC değildir; hafta sonu boşluğu varsa 16:40 mumları kapanış olarak DC dışıdır.
  - app90 (90m): 18:00; (Pazar hariç) 19:30; Cuma 16:30 DC olamaz.
  - app96 (96m): 18:00; (Pazar hariç) 19:36; Cuma 16:24 DC olamaz.
  - app120 (120m): 18:00 DC değildir; (Pazar hariç) 20:00 DC olamaz; Cuma 16:00 DC sayılmaz (hafta kapanışı).
- Kapsayıcı kural: Dizi adımı DC’ye denk gelirse timestamp DC mumuna yazılır. +Offset başlangıçlarında DC varsa “normal mum” gibi sayılır.

### 3.5 Offset Sistemi ve Hizalama
- Başlangıç: Veri setindeki ilk 18:00 mumu. Offset aralığı `-3..+3`, hedef zaman = offset × timeframe dakikası.
- Pozitif offset: Başlangıç noktası DC olmayan ilk muma kaydırılır; eksik veri varsa `missing_steps` hesaplanır ve tahmini zaman (`pred`) yazılır.
- Negatif offset: Hedef zaman doğrudan kullanılır; DC olsa da geri arama yapılmaz.
- Matrix görünümleri tüm offsetleri tek tabloda gösterir, bulunamayan yerler `pred` ile doldurulur.

### 3.6 Hafta Sonu ve Tahmin
- Tahmin motoru: app72/app80/app120 hafta sonu kapanışını Pazar 18:00’a sıçrayarak hesaplar; app48/app90/app96/app321 doğrusal dakika ekler.
- Counter CLI’larda `--predict` ve `--predict-next` desteklenir (app48 hariç). Web analiz tablolarında veri dışı satırlar “pred” olarak işaretlenir.

### 3.7 OC / PrevOC ve app90 PrevOC Toplamı
- OC: `Close - Open`; PrevOC: önceki mumun OC’si. Tahmini satırlarda `OC=- PrevOC=-`.
- app90 `OC/PrevOC` sekmesi: `|PrevOC| ≥ (limit + tolerans)` sağlayan hücreler katkı hesaplar. OC ve PrevOC aynı işaretliyse katkı `-abs(OC)`, zıt işaretliyse `+abs(OC)`; offset başına toplam, pozitif/negatif adetleri raporlanır. Haber filtresi açıksa ilgili haber penceresine denk gelen katkılar atlanır (speech 60 dk pencere, holiday/all-day hariç).

### 3.8 IOU/IOV Eşik Mantığı
- Limit mutlak değerdir; negatif girilirse abs alınır.
- IOU: Etkin eşik `limit + tolerans`; `abs(OC)` ve `abs(PrevOC)` bu eşiğin altında ise elenir; işaret aynı olmalı.
- IOV (app120): Tolerans 0, eşik yalnız limit; işaretler zıt olmalı.
- Limit=0: IOU’da pratik eşik toleranstır (vars 0.005); IOV’da 0.

### 3.9 Dizi “Skip” Kuralı
- IOU/IOV taramalarında S1 için 1 ve 3; S2 için 1 ve 5 sinyal dışıdır.

### 3.10 IOU Zaman Kısıtları
- app321: 18:00, 19:00, 20:00 IOU değildir.
- app48: 18:00, 18:48, 19:36 IOU değildir.
- app72: 15:36, 16:48 her gün IOU dışı; 18:00, 19:12, 20:24 yalnız ikinci Pazar serbest; ilk haftanın Cuma 16:48’i IOU vermez.
- app80: 15:20, 16:40, 18:00 IOU dışı; 19:20/20:40 yalnız Pazar günleri IOU olabilir; Cuma 16:40 IOU vermez.
- app90: 15:00, 16:30, 16:40, 18:00 IOU dışı; (Pazar hariç) 19:30 IOU vermez.
- app96: 14:48, 16:24, 18:00 IOU dışı; (Pazar hariç) 19:36 IOU vermez.
- app120: 16:00 ve 18:00 IOU değildir; 20:00 tüm günlerde IOU vermez; tüm Pazar mumları IOU dışıdır.

### 3.11 XYZ (Haber Filtreli) Kümesi
- Haber kaynağı: `news_loader.py` `economic_calendar/*.json` (kök JSON’lar da taranır); `time_24h` yoksa `time`/`time_text`/`time_label`/`session` denenir. Kategoriler: `holiday`, `all-day`, `speech`, `normal`.
- Hücre etiketi: `Var`, `Holiday`, `AllDay`, `Yok`. `recent-null` penceresi 60 dk.
- app72 slot koruması: Haber listesi boşsa 16:48, 18:00, 19:12, 20:24 offsetleri “Kural slot HH:MM” notuyla korunur (holiday/all-day gelirse kalkar).
- Web IOU’da eleme OR ve `>` ile çalışır (haber yoksa ve `|OC| >` ya da `|PrevOC| >` limit+tolerans), teorik çekirdek AND ve `≥` olarak dokümanlanmış sapma.

### 3.12 Örüntüleme (Pattern) ve Joker
- Tüm IOU sayfalarında örüntüleme seçeneği; Joker işaretli dosya XYZ kümesinde tüm offsetleri (-3..+3) kapsar.
- Kurallar: Başlangıç serbest; üçlü 1–2–3 veya 3–2–1 aynı işaretle tamamlanır; üçlü bitmeden 0 gelmez; ±2 ile başlanırsa 1/3 (aynı işaret) gelir; arka arkaya aynı değer yasak; ilk adım ±1/±3 ise ikinci adımda 0’a izin verilir.
- Görsellik: Tooltip’te dosya adı + “(Joker)”; tekrarlayan üçlü blokları renklendirilir.
- Performans: app48 beam=512, max 1000 örüntü; diğerlerinde sınır yok.

### 3.13 Stacked IOU Akışı
- IOU’lar iki aşamalıdır: önce Joker seçimi, ardından analiz. Önceki sonuçlar `previous_results_html` ile base64 taşınır ve “Önceki Analizler” bölümü gösterilir.
- Stacked: Yeni sonuç “Analiz #YYYYMMDD_HHMMSS” başlığıyla üstte birikir; form yeniden eklenir. app120’de stacking yalnız IOU için geçerlidir (IOV tek seferlik).
- app72/app120 IOU’da örüntü zinciri geçmişi (`previous_pattern_payload`) saklanır; gruplar başlangıç değerine göre “Toplu örüntüler” panelinde listelenir.

## 4) Uygulama Başlıkları

Her uygulama tipik olarak `counter.py` (sayım/sinyal), `main.py` (converter), `web.py` (HTTP arayüz) içerir. Counter CLI’lar `--predict/--predict-next` destekler (app48 hariç).

### 4.1 app48 (48m)
- Port: 2020. Sekmeler: Analiz, DC List (filtreli), Matrix, IOU Tarama, 12→48 Converter (`/convert`).
- Sentetik mumlar: İlk gün hariç her gün 17:12–19:36 arasına 18:00 ve 18:48 eklenir (open/close lineer, high/low min/max), sonra DC yeniden hesaplanır.
- DC istisnası: 13:12–19:36 DC’ler normal; 18:00/18:48/19:36 DC sayılmaz. IOU’da bu üç slot listelenmez.
- IOU: Limit + tolerans (vars 0.005), çoklu CSV, XYZ, özet tablo, örüntüleme + Joker, stacked. Positive offset/DC skip mantığı analiz ve matrix’te de geçerli.
- DC List: `only_syn` / `only_real` filtreleri. Matrix ve analizde veri dışı satırlar “pred” olarak gösterilir.
- Converter: Çoklu 12m CSV (UTC-5 varsayılan), +1h normalize, 4×12m→1×48m; çoklu dosya ZIP iner.

### 4.2 app72 (72m)
- Port: 2172. Sekmeler: Analiz, DC List, Matrix, IOU Tarama, 12→72 Converter.
- DC istisnaları: 18:00; Cuma 16:48; (Pazar hariç) 19:12, 20:24; Cuma 16:00 DC değildir.
- IOU kısıtları: 15:36, 16:48 her gün dışı; 18:00/19:12/20:24 ikinci Pazar serbest; ilk haftanın Cuma 16:48’i IOU vermez.
- IOU: Limit + tolerans (≥), çoklu CSV, XYZ + slot koruması, özet tablo, örüntüleme + Joker, stacked. Örüntü zinciri `Toplu örüntüler` panelinde geçmişle birleşir (bkz. `app72_pattern_chaining.md`).
- Tahmin: Hafta sonu boşluğunu Pazar 18:00’a sıçrayarak hesaplar.
- Converter: 7×12m→1×72m; Cumartesi ve Pazar 18:00 öncesi atlanır; çoklu dosya destekli (ZIP çıktısı).

### 4.3 app80 (80m)
- Port: 2180. Sekmeler: Analiz, DC List, Matrix, IOU Tarama, 20→80 Converter.
- DC/IOU kısıtları: (Pazar hariç) 18:00, 19:20, 20:40 DC/IOU dışı; Cuma 16:40 kapanış DC/IOU dışıdır; 19:20/20:40 yalnız Pazar IOU verebilir.
- IOU: Limit + tolerans, çoklu CSV, XYZ, örüntüleme + Joker, stacked. Tahminler hafta sonu boşluğunu atlar.
- Converter: 4×20m→1×80m; Cumartesi ve Pazar 18:00 öncesi atlanır; çoklu dosya ZIP.

### 4.4 app90 (90m)
- Port: 2190. Sekmeler: Analiz, DC List, Matrix, OC/PrevOC, IOU Tarama, 30→90 Converter.
- DC kısıtları: 18:00; (Pazar hariç) 19:30; Cuma 16:30 DC değildir.
- IOU kısıtları: 15:00, 16:30, 16:40, 18:00 IOU dışı; (Pazar hariç) 19:30 IOU vermez.
- IOU: Limit + tolerans, çoklu CSV, XYZ, örüntüleme + Joker, stacked.
- OC/PrevOC sekmesi: `|PrevOC| ≥ limit+tolerans` koşuluyla katkı toplar; zıt işaret +, aynı işaret −; haber filtresi seçilirse ilgili haber penceresine denk gelen katkılar atlanır; offset başına toplam/pozitif/negatif sayıları raporlanır.
- Converter: 3×30m→1×90m; Cumartesi ve Pazar 18:00 öncesi atlanır; çoklu dosya ZIP.

### 4.5 app96 (96m)
- Port: 2196. Sekmeler: Analiz, DC List, Matrix, IOU Tarama, 12→96 Converter.
- DC/IOU kısıtları: 18:00; (Pazar hariç) 19:36; Cuma 16:24 DC değildir; IOU’da ek 14:48 ve 16:24 dışıdır.
- IOU: Limit + tolerans, çoklu CSV, XYZ, örüntüleme + Joker, stacked. Tahminler doğrusal.
- Converter: 8×12m→1×96m; Cumartesi ve Pazar 18:00 öncesi atlanır; çoklu dosya ZIP.

### 4.6 app120 (120m)
- Port: 2120. Sekmeler: Analiz, DC List, Matrix, IOV Tarama, IOU Tarama, 60→120 Converter.
- DC istisnaları: 18:00 DC değildir; (Pazar hariç) 20:00 DC olamaz; Cuma 16:00 DC sayılmaz.
- IOU kısıtları: 16:00, 18:00 her gün; 20:00 tüm günlerde IOU dışı; tüm Pazar mumları IOU dışıdır.
- IOU: Limit + tolerans, çoklu CSV, XYZ, örüntüleme + Joker, stacked (örüntü zinciri app72 ile aynı mantıkta).
- IOV: Limit (tolerans yok), zıt işaretli çiftler; stacked değildir.
- Tahmin: Hafta sonu boşluğunu Pazar 18:00’a sıçrayarak hesaplar.
- Converter: 60→120 normalize eder, indirilebilir CSV; çoklu dosya ZIP.

### 4.7 app321 (60m)
- Port: 2019. Sekmeler: Analiz, DC List, Matrix, IOU Tarama.
- DC istisnaları: 13:00–20:00 arası DC’ler normal kabul; 20:00 (Pazar hariç) DC olamaz.
- IOU kısıtları: 18:00, 19:00, 20:00 IOU değildir.
- IOU: Limit + tolerans, çoklu CSV, XYZ, örüntüleme + Joker, stacked. IOU dosya sınırı 25.
- Tahmin: Doğrusal; pozitif offset DC atlama kuralı geçerli.

## 5) Web Katmanı ve Ortak Araçlar

### 5.1 landing
- `python3 -m landing.web --port 2000` kartlı landing üretir; `photos/` altındaki görselleri ve favicon linklerini kullanır.

### 5.2 appsuite (Reverse Proxy)
- Tüm uygulamaları tek host altında path bazlı sunar (örn. `/app72`). İç servisleri ayrı thread’lerde varsayılan 92xx portlarda başlatır; health: `/health` → `ok`.
- HTML içindeki `href` ve `action` değerlerini prefix’e göre rewrite eder.
- Güvenlik başlıkları ekler; upload guard 50 MB.

### 5.3 calendar_md
- Web: `python3 -m calendar_md.web --port 2300` — çoklu `.md` yükler (max 25 dosya), her biri için JSON üretir; çokluysa ZIP. Formda yıl/timezone/source alanları vardır.
- CLI: `python3 -m calendar_md --input takvim.md --output out.json --year 2025 --timezone UTC-4 --source markdown_import`.

### 5.4 news_loader
- `economic_calendar/*.json` ve kök JSON’ları birleştirip önbellekler. Eksik saat alanlarında alternatif adları dener. Kategoriler: `holiday`, `all-day`, `speech`, `normal`. All-day kayıtlar gün bazlı eşleşir; `recent-null` penceresi desteklenir.

### 5.5 favicon
- `render_head_links` ile tüm HTML’de kullanılan link seti; `/favicon.ico`, manifest ve boyutlu PNG’ler servis edilir.

## 6) Dağıtım ve Çalıştırma

1) Python 3.11+.  
2) (İsteğe bağlı) `python3 -m venv .venv && source .venv/bin/activate`  
3) `pip install -r requirements.txt` (`gunicorn`).  
4) CLI örneği: `python3 -m app120.counter --csv data.csv --sequence S2 --offset 0`.  
5) Web örneği: `python3 -m app120.web --host 0.0.0.0 --port 2120`.  
6) appsuite: `python3 -m appsuite.web --host 0.0.0.0 --port 2000` (iç portlar argümanla değiştirilebilir).  
7) Üretim: `gunicorn app120.web:main` benzeri komutlar.

## 7) Geliştirici Notları

- Counter CLI’lar UTC-4 veri bekler (app48 hariç). Web ve converter’lar `input_tz` ile normalize eder.
- IOU eşiği: `≥ (limit + tolerans)`; IOV’da tolerans yok. Dizi skip kuralı tüm taramalarda geçerlidir.
- app48 `/convert` rotası diğerlerinden farklıdır (diğerleri `/converter`).
- IOU özet modunda ayrıntılı tablo yerine XYZ kümesi + elenen offsetler listelenir; örüntü paneli yine eklenir.
- Haber filtresi: IOU’da XYZ mantığı (web OR sapması); app90 OC/PrevOC sekmesinde haber filtresi katkı satırlarını tamamen atlar.
- Güvenlik başlıkları tüm web katmanlarında set edilir; tooltipler için CSP’de `unsafe-inline` stil izni vardır.
- Dosya adları sanitize edilir, tekrar edenlerde sayısal ek yapılır.

## 8) Sorun Giderme ve İpuçları

- Limit=0 çoğu sinyali eler; IOU’da varsayılan tolerans (0.005) pratik eşiği belirler.
- DC List sekmeleri ham DC’leri CSV’ye çıkarmak için uygundur (app48’te syn/real ayrımı).
- Örüntü aramaları app48’te beam=512 / max 1000; diğerlerinde sınırsız ancak büyük girişlerde yavaş olabilir.
- app72’de ikinci Pazar kuralı ve slot korumasını unutma; app120’de tüm Pazar IOU dışı.
- IOU sonuçlarındaki “(rule)” etiketi DC kapsamasıyla, “(syn)” etiketi sentetik mumla ilişkilidir.
- Çoklu dosya dönüştürücülerde giriş timeframe’inin doğru olduğundan emin ol (aksi halde hata verir).

— Son —
