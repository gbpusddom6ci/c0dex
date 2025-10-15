# Proje Rehberi

Bu rehber, projedeki tüm alt uygulamaları (app321, app48, app72, app80, app120), destekleyici web katmanlarını ve veri kurallarını tek bir dokümanda toplar. Amaç, yeni devralan bir geliştiricinin veya başka bir yapay zekâ ajanın kod tabanını hiçbir ek kaynağa ihtiyaç duymadan anlayabilmesidir.

## 1. En Güncel Özellikler

- **2025-08 – app48/app72/app80/app321 IOU sekmeleri:** Her timeframe için IOU taraması eklendi; formlar çoklu CSV yüklemelerini destekler ve sonuçlar dosya bazlı kartlarda rapor edilir.
- **2025-08 – app120 IOV/IOU çoklu dosya taraması:** IOV ve IOU web sekmeleri tek seferde birden fazla CSV dosyası kabul eder; her dosyanın sonuçları ayrı kartlarda raporlanır. Limit, dizi ve zaman dilimi ayarı tüm yüklemelere aynı anda uygulanır.
- **2025-08 – IOU sinyal motoru:** IOV’e paralel olarak IOU (aynı işaretli OC/PrevOC) algılama eklendi.
- **2025-08 – IOV sinyal motoru:** OC/PrevOC limit eşikleri ve işaret kontrolleriyle “Inverse Offset Value” mumları tanımlandı.
- **2025-07 – app120 birleşik web arayüzü:** 120m analiz, DC listesi, offset matrisi ve 60→120 converter tek arayüzde birleştirildi.
- **2025-09 – IOU XYZ filtresi & haber entegrasyonu:** Tüm IOU sekmelerine opsiyonel XYZ filtresi eklendi; tatiller, all-day haberler ve app72’nin 16:48/18:00/19:12/20:24 slotları özel olarak ele alınır.
- **2025-10 – IOU tolerans parametresi:** IOU taramaları için ± tolerans alanı eklendi; varsayılan 0.005 olup UI’dan değiştirilebilir. Sinyaller ancak `|OC|` ve `|PrevOC|` değerleri `limit + tolerans` eşiğini aştığında listelenir.
- **2025-06 – app80 & app72 converter’ları:** 20→80 ve 12→72 dakikalık dönüştürücüler web ve CLI olarak eklendi.
- **2025-05 – app48 sentetik mum desteği:** Piyasa kapanış aralığını korumak için 18:00 ve 18:48 sentetik mumları eklendi.
- **Daha eski çekirdek:** app321 (60m) sayımı, DC tespiti, offset matrisi ve tahmin desteği.

## 2. Mimari Genel Bakış

### 2.1 Teknoloji Yığını
- Python 3.11+. `.python-version` dosyası Render dağıtımı için Python sürümünü kilitler.
- Standart kütüphane ağırlıklı; web arayüzleri `http.server` tabanlı minimal HTTP sunucuları kullanır.
- Üretim için `gunicorn` (bkz. `requirements.txt`). Pandas/Numpy opsiyonel ve varsayılan olarak kullanılmıyor.

### 2.2 Dizin Yapısı (Yeni → Eski)
- `app120/` – 120 dakikalık analiz paketi (counter, converter, web UI).
- `app80/`, `app72/`, `app48/`, `app321/` – diğer timeframe uygulamaları (CLI + web).
- `appsuite/` – Tüm uygulamaları tek host altında reverse proxy’leyen birleşik arayüz.
- `landing/` – Basit tanıtım sayfası, uygulama linklerini listeler.
- `calendar_md/` – ForexFactory tarzı markdown takvimlerini JSON’a dönüştüren CLI + web aracı.
- `favicon/` – Ortak favicon ve manifest varlıklarını sağlayan yardımcı paket.
- `economic_calendar/` – Haber entegrasyonu için örnek JSON takvim dosyaları.
- Kök dizinde varsayılan CSV örnekleri yer almıyor; test için kendi veri setinizi eklemeniz gerekiyor.

### 2.3 Ortak Modül Kalıbı
Her timeframe klasöründe tipik olarak şu modüller bulunur:
- `counter.py` / `main.py` – CLI aracı; offsetli sequence sayımını veya converter’ı yürütür.
- `web.py` – Minimal HTTP sunucusu; HTML formları ve sonuç tabloları.
- `__init__.py` – paket bildirimi.

### 2.4 Veri Akışı
1. CSV dosyası yüklenir, başlık eş anlamlılarıyla normalize edilir.
2. Girdi `UTC-5` ise tüm timestamp’ler +1 saat kaydırılarak `UTC-4`’e normalize edilir.
3. (app48) Sentetik mumlar eklenir; (diğerleri) veri sıralaması korunur.
4. DC bayrakları hesaplanır → global + uygulamaya özel istisnalar uygulanır.
5. Sequence dizisi, offset süreleri boyunca DC olmayan mumlarla eşleştirilir; gerektiğinde DC kapsayıcı kuralı devreye girer.
6. OC (`Close - Open`) ve PrevOC farkları raporlanır; eksik veri için tahmin zamanları hesaplanır.
7. app120 IOV/IOU sekmeleri ek olarak limit ve işaret kontrolleri yapar.

## 3. Ortak Kavramlar

### 3.1 CSV Formatı
Gerekli sütunlar: `Time`, `Open`, `High`, `Low`, `Close (Last)` (eş anlamlılar desteklenir). Bozuk satırlar atlanır; veri timestamp’e göre sıralanır.

### 3.2 Sequence Dizileri
- **S1:** `1, 3, 7, 13, 21, 31, 43, 57, 73, 91, 111, 133, 157`
- **S2:** `1, 5, 9, 17, 25, 37, 49, 65, 81, 101, 121, 145, 169`

### 3.3 Distorted Candle (DC)
Bir mum DC sayılırsa: `High ≤ prev.High`, `Low ≥ prev.Low`, `Close` değeri önceki mumun `[Open, Close]` aralığındadır. Aynı anda iki DC olamaz (ardışık DC engellenir). Varsayılan olarak 18:00 mumu asla DC olmaz.

**İstisna Saatleri:**
- app321: 13:00–20:00 arasında DC’ler normal mum kabul edilir.
- app48: 13:12–19:36 arasında DC’ler normal mum kabul edilir.
- app72: 18:00 (Pazar dahil), Cuma 16:48, (Pazar hariç) 19:12 ve 20:24, Cuma 16:00 DC olamaz.
- app80: (Pazar hariç) 18:00, 19:20, 20:40; Cuma 16:00 DC olamaz.
- app120: İstisna yok; yalnızca kapsayıcı kural uygulanır. Cuma 16:00 hafta kapanışı DC sayılmaz.

**Kapsayıcı Kural:** Bir sequence adımı DC’ye denk gelirse zaman damgası o DC mumuna yazılır.
**Pozitif Offset İstisnası:** Offset +1, +2 ve +3 için başlangıç mumu DC ise ilgili offset özelinde normal mum gibi sayılır (DC kuralı uygulanmaz).

### 3.4 Offset Sistemi
- Başlangıç noktası: yakalanan ilk 18:00 mumu.
- Offset değerleri `-3..+3` arasıdır; timeframe dakika değeriyle çarpılarak hedef zaman belirlenir.
- Hedef mum bulunamazsa (veri yoksa) tahmini saatler `pred` etiketiyle raporlanır.

**Pozitif Offset DC Akışı:** 18:00 baz mumundan itibaren offset adımları, sequence sayımındaki gibi DC olmayan mumlar üzerinden ilerletilir. Örnek akış (`jun01.csv`):

- `+1` offset: 18:00 sonrası ilk gerçek mum 20:00 olduğundan başlangıç 20:00.
- `+2` offset: 22:00 mumu DC olduğu için sayılmaz; sıradaki gerçek mum 00:00 olur.
- `+3` offset: +2’nin devamında bir sonraki gerçek mum 02:00’dır.

Bu yaklaşım, DC’lerin ardışık offset sütunlarını aynı zaman damgasına sabitlemesini engeller. Eğer 20:00 ve 00:00 aynı anda DC ise sayım 22:00 → 02:00 → 04:00 diye devam eder; yani her pozitif offset “bir sonraki DC olmayan mumu” seçer.

**Uygulama Bazlı Notlar:**
- `app321` (60m) pozitif offset başlangıçlarını DC olmayan mumlara kaydırır; DC istisnası yalnızca Pazartesi–Cumartesi 13:00 ≤ saat < 20:00 için geçerlidir. Pazar günleri veya 20:00 mumu istisna dışıdır.
- `app72` ve `app80` (72m / 80m) aynı mantığı kendi dakika adımları ile uygular. Pozitif offset teorik hedefi DC ise sayaç uygun gerçek muma ilerleyip diziyi 72/80 dakikalık farkla korur.
- `app48` (48m) sentetik mumlar üretir; pozitif offset hesaplaması yalnızca gerçek DC sayılan (Pazartesi–Cumartesi 13:12 ≤ saat < 19:36) mumları atlar. Pazar günleri ve 19:36 dahilindeki mumlar normal DC kontrolüne tabidir. Sentetik saatlerdeki DC istisnası korunur, bu sayede offset sütunları yine çakışmaz.
- Negatif offsetlerde veri zaten 18:00’dan önce bulunmadığından ekstra işleme gerek yoktur; mevcut tahmin mantığı olduğu gibi bırakılır.

### 3.5 OC / PrevOC
- **OC:** `Close - Open` (her gerçek mum için raporlanır, `+/-` işaretli 5 hane).
- **PrevOC:** Bir önceki mumun OC değeri; yoksa `-`.
- Tahmini satırlarda `OC=- PrevOC=-` gösterilir.

### 3.6 Zaman Dilimi
- Girdi seçenekleri: `UTC-4` veya `UTC-5`.
- `UTC-5` seçilirse tüm mumlar +60 dakika kaydırılır ve çıktı `UTC-4`’e normalize edilir.

### 3.7 IOU Limit & Tolerans
- Web formları ve CLI çağrıları (varsayılan olarak 0.005) için `± tolerans` değeri desteklenir.
- Bir mum IOU sinyaline dahil olabilmek için hem `|OC|` hem de `|PrevOC|` değerleri `limit + tolerans` eşiğini aşmalıdır; sınırın altında kalan veya yalnızca tolerans içinde kalan değerler elenir.
- Tolerans sıfırlanırsa klasik davranış (yalnızca limit üstü değerler) korunur.

## 4. Uygulama Detayları (Yeni → Eski)

### 4.1 app120 – 120 Dakikalık Analiz Platformu
- **Modüller:**
  - `counter.py` – 120m sequence sayımı, tahmin & DC analizi.
  - `main.py` – 60m → 120m converter (CLI).
  - `web.py` – Altı sekmeli web arayüzü.
- **Web Sekmeleri (port 2120):**
  1. **Analiz:** Sequence listesi, OC/PrevOC, DC bilgisi (`show_dc` seçeneği).
  2. **DC List:** Tüm DC mumlarının ham OHLC çıktısı.
  3. **Matrix:** Tüm offset değerleri için zaman/OC/PrevOC özet tablosu.
  4. **IOV Tarama:** Çoklu CSV desteği; limit eşiklerini aşan ve zıt işaretli OC/PrevOC ikililerini dosya bazlı kartlarda listeler. S1 için `1` ve `3`, S2 için `1` ve `5` sinyal dışıdır. DC kapsaması `(rule)` etiketi ile görünür.
  5. **IOU Tarama:** IOV ile aynı arayüz; farkı aynı işaretli OC/PrevOC’e odaklanmasıdır. Formda varsayılan değeri 0.005 olan `± tolerans` alanı bulunur ve `|OC|`, `|PrevOC|` değerlerinin `limit + tolerans` eşiğini aşmadığı satırlar otomatik olarak elenir.
  6. **60→120 Converter:** 60m CSV yüklenir, normalize edilir, 120m çıktısı CSV indirilebilir.
- **CLI Örnekleri:**
  ```bash
  python3 -m app120.counter --csv data.csv --sequence S2 --offset +1 --show-dc
  python3 -m app120.counter --csv data.csv --predict 37
  python3 -m app120 --csv 60m.csv --input-tz UTC-5 --output 120m.csv
  ```
- **IOV/IOU Limit Mantığı:** Limit mutlak değerdir (0.1 → `|OC| ≥ 0.1`). Limit negatif girilirse `abs(limit)` alınır. Limit=0 durumunda sadece sıfır olmayan değerler eşik üstü kabul edilir.
- **Çoklu Dosya Akışı:** Formdaki tüm CSV’ler aynı sequence/limit/TZ ile işlenir; her dosya için veri kapsamı, offset özetleri ve tablolar ayrı kartlarda sunulur.

### 4.2 app80 – 80 Dakikalık Analiz
- **Üç ana modül:** `counter.py`, `main.py` (20→80 converter), `web.py` (port 2180, sekmeler: Analiz, DC List, Matrix, IOU Tarama, 20→80 Converter).
- **IOU Tarama:** Limit, ± tolerans (varsayılan 0.005) ve dizi seçimiyle aynı işaretli OC/PrevOC ikililerini çoklu CSV desteğiyle dosya bazında listeler; yalnızca `|OC|` ve `|PrevOC|` değerleri `limit + tolerans` eşiğini geçen satırlar raporlanır.
- **DC Kısıtları:** (Pazar hariç) 18:00, 19:20, 20:40; Cuma 16:00 DC olamaz. Önceki DC yasağı geçerlidir.
- **Converter:** 4 × 20m mum → 1 × 80m mum. Open=ilk open, Close=son close, High/Low blok içindeki max/min.
- **CLI Örnekleri:**
  ```bash
  python3 -m app80.counter --csv data.csv --sequence S1 --offset -2
  python3 -m app80.main --csv 20m.csv --input-tz UTC-5 --output 80m.csv
  ```

### 4.3 app72 – 72 Dakikalık Analiz
- **Modüller:** `counter.py`, `main.py` (12→72 converter), `web.py` (port 2172; sekmeler: Analiz, DC List, Matrix, IOU Tarama, 12→72 Converter).
- **IOU Tarama:** Çoklu CSV desteğiyle aynı işaretli OC/PrevOC eşiklerini raporlar; limit + ± tolerans (varsayılan 0.005) eşiğini aşan satırlar dosya kartlarında gösterilir.
- **DC Kısıtları (2 haftalık veri varsayımı):** 18:00, Cuma 16:48, (Pazar hariç) 19:12 & 20:24, Cuma 16:00 DC olamaz.
- **Converter:** 7 adet 12m mum → 1 adet 72m mum (Pazar 18:00 öncesi ve Cumartesi mumları atlanır). Haftasonu boşlukları otomatik geçilir.
- **CLI Örnekleri:**
  ```bash
  python3 -m app72.counter --csv data.csv --sequence S2 --predict-next
  python3 -m app72.main --csv 12m.csv --input-tz UTC-5 --output 72m.csv
  ```

### 4.4 app48 – 48 Dakikalık Analiz
- **Özellikler:** Sentetik mum ekleme (ilk gün hariç, her gün 18:00 ve 18:48). Web portu 2020.
- **IOU Tarama:** Limit, ± tolerans (varsayılan 0.005) ve dizi seçimleriyle çoklu CSV analiz eder; `limit + tolerans` eşiğini geçen satırlar sentetik/gerçek ayrımı `syn/real` etiketiyle gösterilerek raporlanır.
- **Sentetik Mum Akışı:** 17:12 ve 19:36 gerçek mumları arasına 18:00/18:48 sentetik mumlar eklenir; open/close lineer şekilde setlenir (open = önceki close, close = sonraki open’a doğru interpolasyon, high/low min/max).
- **DC İstisnası:** 13:12–19:36 arası DC’ler normal kabul edilir.
- **CLI Örnekleri:**
  ```bash
  python3 -m app48.main --csv data.csv --input-tz UTC-5 --sequence S2 --offset +1 --show-dc
  python3 -m app48.main --csv data.csv --predict 49
  ```

### 4.5 app321 – 60 Dakikalık Analiz
- **Port 2019** için web arayüzü; sekmeler: Analiz, DC List, Matrix, IOU Tarama.
- **IOU Tarama:** Multi-upload desteği; kullanıcı limit ve ± tolerans (varsayılan 0.005) belirler, `|OC|`, `|PrevOC| ≥ limit + tolerans` koşulunu sağlayan aynı işaretli değerler offset bazında listelenir.
- **DC İstisnası:** 13:00–20:00 arası DC’ler normal mum sayılır.
- **Tahmin:** Sequence değerleri veri aralığı dışına taşarsa tahmini timestamp raporlanır.
- **Matrix Sekmesi:** Tüm offset değerleri tek tabloda saat/OC/PrevOC olarak listelenir.
- **CLI Örnekleri:**
  ```bash
  python3 -m app321.main --csv data.csv --sequence S1 --offset -3 --show-dc
  python3 -m app321.main --csv data.csv --predict-next
  ```

## 5. Web Katmanı ve Birleşik Arayüzler

### 5.1 landing
- `python3 -m landing.web --port 2000` ile çalışır; kart tabanlı landing sayfası üretir.
- Uygulama URL’leri komut satırı argümanlarıyla değiştirilebilir.

### 5.2 appsuite
- Reverse proxy görevi görür; tüm uygulamaları tek host altında farklı path’lerle (`/app48`, `/app72`, ...).
- Her backend ayrı thread’de başlatılır (`start_backend_thread`). HTML linkleri proxy prefix’ine göre rewrite edilir.
- Health endpoint: `/health` → `ok`.

### 5.3 calendar_md
- `python3 -m calendar_md.web --port 2300` komutu markdown → JSON dönüştürücüyü tarayıcıda açar.
- Çoklu `.md` yüklemelerini kabul eder; her dosya için ayrı JSON üretip zip arşivi halinde indirir.
- CLI modu: `python3 -m calendar_md --input takvim.md --output out.json --year 2025`.
- Üretilen dosyalar `news_loader.py` tarafından IOU XYZ filtresi için tüketilir.

### 5.4 Dağıtım Dosyaları
- `Procfile` ve `render.yaml` Render.com dağıtımı için örnek konfigürasyon sağlar.
- `Dockerfile` minimal Python imajıyla tüm web servislerini başlatmaya uygun temel sunar.
- `railway.toml` Railway/Nixpacks dağıtımı için varsayılan komutları tanımlar.
- `.python-version` (3.11.0) Render ve lokal geliştirmede tutarlı runtime seçimini garanti eder.

### 5.5 IOU Haber Akışı & XYZ Filtresi (2025-09)
- **Checkbox:** app48/app72/app80/app120/app321 IOU formlarında “XYZ kümesi (haber filtreli)” seçeneği bulunur. İşaretlendiğinde haber taşımayan offsetler elenir ve kalanlar kart üst bilgisinde `XYZ Kümesi` satırıyla listelenir.
- **Haber kaynağı (`news_loader.py`):** JSON takvim dosyalarında `time_24h` yoksa `time`, `time_text`, `time_label`, `session` alanlarını dener. `"All Day"` / `all_day=true` kayıtları gün bazında yakalar, `recent-null` penceresi null actual taşıyan önceki olayları dahil eder.
- **Hücre formatı:** Haber sütunu `Var`, `Holiday` veya `Yok` ile başlar. Tatil satırları sadece bilgi amaçlıdır; grafiksel olarak listelenir ama offset elenmesini tetiklemez.
- **Tatiller:** Başlıkta “holiday” geçen olaylar `effective_news=False` sayılır. Sadece tatil içeren bir offset, XYZ kümesinde tutulur; satır `Holiday<br>All Day Bank Holiday (holiday)` gibi görünür.
- **All-day haberler:** Zaman etiketi “All Day – Başlık” formatıyla yazılır. Tatil dışı all-day olayları offset’i korur.
- **17:xx slot kuralı (app72):** `SPECIAL_SLOT_TIMES = {16:48, 18:00, 19:12, 20:24}`. Haber listesi boş olsa bile bu saatler “Kural slot HH:MM” notuyla haber var kabul edilir ve elenmez. Slotta tatil varsa nötr kalır; yalnızca gerçek olmayan tatiller filtreyi tetiklemez.
- **Boş haberler:** Haber bulunmazsa hücre `Yok` olur ve offset elenir. Böylece yalnızca haber (veya özel slot) olmayan kombinasyonlar XYZ dışına atılır.
- **Tolerans entegrasyonu:** XYZ filtresi hesaplanırken de `|OC|`, `|PrevOC| ≥ limit + tolerans` şartı aranır; tolerans dahilinde kalan satırlar otomatik olarak filtre dışına taşınır.

### 5.6 Ortak Varlıklar
- `favicon` paketi tüm web arayüzlerinde kullanılan favicon ve manifest dosyalarını sunar (`render_head_links` + `try_load_asset`).
- Appsuite reverse proxy’si favicon isteklerini doğrudan bu paket üzerinden cevaplar; harici CDN gerektirmez.

## 6. Veri Setleri ve Örnek Dosyalar
- Repoda hazır CSV örnekleri bulunmuyor; test etmek için kendi veri setlerinizi eklemelisiniz.
- IOU/IOV senaryolarını doğrulamak için web arayüzlerinde çoklu dosya yükleme özelliğini kullanabilirsiniz.
- Haber filtresi `economic_calendar/` klasöründeki JSON dosyalarından beslenir; yeni takvimler `calendar_md` araçlarıyla üretilebilir.

## 7. Kurulum ve Çalıştırma

1. Python 3.11+ kurulu olmalı.
2. (İsteğe bağlı) sanal ortam oluştur: `python3 -m venv .venv && source .venv/bin/activate`.
3. Bağımlılıkları yükle: `pip install -r requirements.txt` (yalnızca `gunicorn`).
4. CLI örneği: `python3 -m app120.counter --csv data.csv`.
5. Web örneği: `python3 -m app120.web --host 0.0.0.0 --port 2120` (veya `appsuite` ile birleşik servis).
6. Üretim için `gunicorn app120.web:main` benzeri komutlar Customize edilmelidir.

## 8. Kullanım İpuçları

- **IOV/IOU Çoklu Yükleme:** 25’e kadar CSV aynı formla seçilebilir; sonuçlarda hangi dosyanın hangi sinyali verdiği açıkça görülür.
- **Limit Seçimi:** Limit değeri 0 girilmemelidir; sıfır değeri sinyallerin çoğunu eler.
- **± Tolerans:** Varsayılan tolerans 0.005’tir; IOU formlarındaki alanı kullanarak eşiği genişletebilir/kısıtlayabilirsiniz. Tolerans değeri `limit`e eklenir, bu yüzden toleransı büyütmek raporlanan satırları daraltır.
- **DC İncelemesi:** Web arayüzlerindeki DC List sekmeleri ile ham DC listelerini görüntüleyebilir ve CSV’ye aktarabilirsiniz.
- **Timezone Tutarlılığı:** Render’da güncel veri yüklerken girdi timezone’unu mutlaka seçin; aksi halde analiz kayar.
- **Sentetik Mumlar:** app48 sonuçlarında sentetik mumlar normal count’a dahil, ancak DC listesinde `tag=syn` ile ayrışır.

## 9. Geliştirici Notları

- `__pycache__` klasörleri version control’de tutulmamalı; geliştirme sırasında otomatik oluşur.
- CSV dosyaları büyükse (25 dosya yükleme) tarayıcı POST limitini aşmamak için boyut kontrolü yapın.
- Yeni timeframe eklemek için en güncel örnek olarak `app120` mimarisini baz alın; ortak kurallar `CounterCandle` ve DC hesaplama fonksiyonlarıyla paylaşılabilir.
- Render dağıtımında her web servisinin ayrı port’ta koştuğundan emin olun; `appsuite` tümünü proxy’lemek için en pratik çözüm.

Bu doküman, proje kapsamı genişledikçe güncellenmelidir. Yeni bir özellik eklendiğinde, “En Güncel Özellikler” bölümüne tarih/özet eklemeyi unutmayın.
