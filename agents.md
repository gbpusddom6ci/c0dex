# Proje Rehberi

Bu rehber, projedeki tüm alt uygulamaları (app321, app48, app72, app80, app120), destekleyici web katmanlarını ve veri kurallarını tek bir dokümanda toplar. Amaç, yeni devralan bir geliştiricinin veya başka bir yapay zekâ ajanın kod tabanını hiçbir ek kaynağa ihtiyaç duymadan anlayabilmesidir.

## 1. En Güncel Özellikler

- **2025-08 – app48/app72/app80/app321 IOU sekmeleri:** Her timeframe için IOU taraması eklendi; formlar çoklu CSV yüklemelerini destekler ve sonuçlar dosya bazlı kartlarda rapor edilir.
- **2025-08 – app120 IOV/IOU çoklu dosya taraması:** IOV ve IOU web sekmeleri tek seferde birden fazla CSV dosyası kabul eder; her dosyanın sonuçları ayrı kartlarda raporlanır. Limit, dizi ve zaman dilimi ayarı tüm yüklemelere aynı anda uygulanır.
- **2025-08 – IOU sinyal motoru:** IOV’e paralel olarak IOU (aynı işaretli OC/PrevOC) algılama eklendi.
- **2025-08 – IOV sinyal motoru:** OC/PrevOC limit eşikleri ve işaret kontrolleriyle “Inverse Offset Value” mumları tanımlandı.
- **2025-07 – app120 birleşik web arayüzü:** 120m analiz, DC listesi, offset matrisi ve 60→120 converter tek arayüzde birleştirildi.
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
- `app48_dc/`, `app321_dc/` – yalnızca DC listesi çıkaran yardımcı CLI’lar.
- `appsuite/` – Tüm uygulamaları tek host altında reverse proxy’leyen birleşik arayüz.
- `landing/` – Basit tanıtım sayfası, uygulama linklerini listeler.
- Kök dizindeki `.csv` dosyaları test/örnek veri setleri.

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

### 3.5 OC / PrevOC
- **OC:** `Close - Open` (her gerçek mum için raporlanır, `+/-` işaretli 5 hane).
- **PrevOC:** Bir önceki mumun OC değeri; yoksa `-`.
- Tahmini satırlarda `OC=- PrevOC=-` gösterilir.

### 3.6 Zaman Dilimi
- Girdi seçenekleri: `UTC-4` veya `UTC-5`.
- `UTC-5` seçilirse tüm mumlar +60 dakika kaydırılır ve çıktı `UTC-4`’e normalize edilir.

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
  5. **IOU Tarama:** IOV ile aynı arayüz; farkı aynı işaretli OC/PrevOC’e odaklanmasıdır.
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
- **IOU Tarama:** Limit ve dizi seçimiyle aynı işaretli OC/PrevOC ikililerini çoklu CSV desteğiyle dosya bazında listeler.
- **DC Kısıtları:** (Pazar hariç) 18:00, 19:20, 20:40; Cuma 16:00 DC olamaz. Önceki DC yasağı geçerlidir.
- **Converter:** 4 × 20m mum → 1 × 80m mum. Open=ilk open, Close=son close, High/Low blok içindeki max/min.
- **CLI Örnekleri:**
  ```bash
  python3 -m app80.counter --csv data.csv --sequence S1 --offset -2
  python3 -m app80.main --csv 20m.csv --input-tz UTC-5 --output 80m.csv
  ```

### 4.3 app72 – 72 Dakikalık Analiz
- **Modüller:** `counter.py`, `main.py` (12→72 converter), `web.py` (port 2172; sekmeler: Analiz, DC List, Matrix, IOU Tarama, 12→72 Converter).
- **IOU Tarama:** Çoklu CSV desteğiyle aynı işaretli OC/PrevOC eşiklerini raporlar; sonuçlar dosya kartlarında gösterilir.
- **DC Kısıtları (2 haftalık veri varsayımı):** 18:00, Cuma 16:48, (Pazar hariç) 19:12 & 20:24, Cuma 16:00 DC olamaz.
- **Converter:** 7 adet 12m mum → 1 adet 72m mum (Pazar 18:00 öncesi ve Cumartesi mumları atlanır). Haftasonu boşlukları otomatik geçilir.
- **CLI Örnekleri:**
  ```bash
  python3 -m app72.counter --csv data.csv --sequence S2 --predict-next
  python3 -m app72.main --csv 12m.csv --input-tz UTC-5 --output 72m.csv
  ```

### 4.4 app48 – 48 Dakikalık Analiz
- **Özellikler:** Sentetik mum ekleme (ilk gün hariç, her gün 18:00 ve 18:48). Web portu 2020.
- **IOU Tarama:** Limit ve dizi seçimleriyle çoklu CSV analiz eder; sonuç tabloları sentetik/gerçek ayrımını `syn/real` etiketiyle gösterir.
- **Sentetik Mum Akışı:** 17:12 ve 19:36 gerçek mumları arasına 18:00/18:48 sentetik mumlar eklenir; open/close lineer şekilde setlenir (open = önceki close, close = sonraki open’a doğru interpolasyon, high/low min/max).
- **DC İstisnası:** 13:12–19:36 arası DC’ler normal kabul edilir.
- **CLI Örnekleri:**
  ```bash
  python3 -m app48.main --csv data.csv --input-tz UTC-5 --sequence S2 --offset +1 --show-dc
  python3 -m app48.main --csv data.csv --predict 49
  ```
- **app48_dc CLI:** DC listesini çıkarır, sentetik mumları `tag=syn` etiketiyle gösterir.

### 4.5 app321 – 60 Dakikalık Analiz
- **Port 2019** için web arayüzü; sekmeler: Analiz, DC List, Matrix, IOU Tarama.
- **IOU Tarama:** Multi-upload desteği; limit eşiğini aşan ve aynı işaretli OC/PrevOC değerlerini offset bazında listeler.
- **DC İstisnası:** 13:00–20:00 arası DC’ler normal mum sayılır.
- **Tahmin:** Sequence değerleri veri aralığı dışına taşarsa tahmini timestamp raporlanır.
- **Matrix Sekmesi:** Tüm offset değerleri tek tabloda saat/OC/PrevOC olarak listelenir.
- **CLI Örnekleri:**
  ```bash
  python3 -m app321.main --csv data.csv --sequence S1 --offset -3 --show-dc
  python3 -m app321.main --csv data.csv --predict-next
  ```
- **app321_dc CLI:** 60m akışı için DC listesini hızlıca çıkarır.

## 5. Web Katmanı ve Birleşik Arayüzler

### 5.1 landing
- `python3 -m landing.web --port 2000` ile çalışır; kart tabanlı landing sayfası üretir.
- Uygulama URL’leri komut satırı argümanlarıyla değiştirilebilir.

### 5.2 appsuite
- Reverse proxy görevi görür; tüm uygulamaları tek host altında farklı path’lerle (`/app48`, `/app72`, ...).
- Her backend ayrı thread’de başlatılır (`start_backend_thread`). HTML linkleri proxy prefix’ine göre rewrite edilir.
- Health endpoint: `/health` → `ok`.

### 5.3 Dağıtım Dosyaları
- `Procfile` ve `render.yaml` Render.com dağıtımı için örnek konfigürasyon sağlar.
- `Dockerfile` minimal Python imajıyla tüm web servislerini başlatmaya uygun temel sunar.

## 6. Veri Setleri ve Örnek Dosyalar
- `x.csv`, `x222.csv`, `test.csv`, `test48.csv`, `test_120m.csv`, `test_offset.csv` – Test veya demo akışları.
- `points.csv` – 120m S1 örnek çıktısı (IOV örneği).
- `120mdata.csv`, `4312.csv`, `ornekdata120.csv`, `tassak*.csv`, `ex12to48.csv` – Çeşitli deneme verileri.

Bu dosyalar git repo’sunda tutuluyor; üretimde kullanılmadan önce uygun klasörlere taşınması önerilir.

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
- **DC İncelemesi:** Şüpheli zaman aralıklarında `app48_dc` veya `app321_dc` CLI’larını kullanarak ham DC listesi çıkarabilirsiniz.
- **Timezone Tutarlılığı:** Render’da güncel veri yüklerken girdi timezone’unu mutlaka seçin; aksi halde analiz kayar.
- **Sentetik Mumlar:** app48 sonuçlarında sentetik mumlar normal count’a dahil, ancak DC listesinde `tag=syn` ile ayrışır.

## 9. Geliştirici Notları

- `__pycache__` klasörleri version control’de tutulmamalı; geliştirme sırasında otomatik oluşur.
- CSV dosyaları büyükse (25 dosya yükleme) tarayıcı POST limitini aşmamak için boyut kontrolü yapın.
- Yeni timeframe eklemek için en güncel örnek olarak `app120` mimarisini baz alın; ortak kurallar `CounterCandle` ve DC hesaplama fonksiyonlarıyla paylaşılabilir.
- Render dağıtımında her web servisinin ayrı port’ta koştuğundan emin olun; `appsuite` tümünü proxy’lemek için en pratik çözüm.

Bu doküman, proje kapsamı genişledikçe güncellenmelidir. Yeni bir özellik eklendiğinde, “En Güncel Özellikler” bölümüne tarih/özet eklemeyi unutmayın.
