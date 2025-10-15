# Agents Knowledge Base

Bu klasördeki dosyalar, tek parça `agents.md` belgesinde toplanmış içerikleri okunabilir alt başlıklara ayrılmış halde sunar. Amaç, yeni bir geliştirici ya da yapay zekâ ajanın projeyi hızlıca kavramasını sağlamak. Detaylı uygulama akışları için aşağıdaki dosyalara göz atabilirsin:

- [app120](app120.md)
- [app80](app80.md)
- [app72](app72.md)
- [app48](app48.md)
- [app321](app321.md)

Orijinal, tek dosyalık rehber hâlâ kökteki `agents.md` dosyasında tutuluyor.

## En Güncel Özellikler
- **2025-10 – IOU tolerans parametresi:** IOU taramalarında `± tolerans` alanı varsayılan 0.005 değeriyle gelir; `|OC|` ve `|PrevOC|` ancak `limit + tolerans` eşiğini aşarsa sonuçlar listelenir.
- **2025-09 – IOU XYZ filtresi & haber entegrasyonu:** Tüm IOU sekmelerinde opsiyonel XYZ filtresi bulunur. Tatiller ve app72’nin 16:48/18:00/19:12/20:24 slotları özel kurallarla işlenir.
- **2025-08 – IOU/IOV sinyal motoru:** IOU (aynı işaretli) ve IOV (zıt işaretli) tespiti için ortak mekanizma oluşturuldu.
- **2025-08 – Çoklu dosya yükleme:** app48/app72/app80/app120/app321 IOU sekmeleri ile app120 IOV sekmesi aynı formda birden fazla CSV’yi işleyebilir.
- **2025-07 – app120 birleşik web arayüzü:** Analiz, DC listesi, offset matrisi, IOV/IOU taramaları ve 60→120 dönüştürücü tek web arayüzünde toplandı.
- **2025-06 – app80 & app72 converter’ları:** 20→80 ve 12→72 dönüştürücüler hem web hem CLI tarafında mevcut.
- **2025-05 – app48 sentetik mum desteği:** Piyasa kapanış aralığında 18:00 ve 18:48 sentetik mumları üretiliyor.
- **Çekirdek:** app321 (60m) sayımı, DC tespiti, offset matrisi ve tahmin desteği önceki sürümlerden devralındı.

## Mimari Özet

### Teknoloji Yığını
- Python 3.11+ (Render dağıtımları için `.python-version` dosyası mevcut).
- Web katmanı standart kütüphane `http.server` tabanlı minimal HTTP servisleri kullanır.
- `gunicorn` prod dağıtımları için hazır; Pandas/Numpy gibi ağır bağımlılıklar zorunlu değil.

### Dizin Yapısı (Yeni → Eski)
- `app120/`, `app80/`, `app72/`, `app48/`, `app321/`: Farklı timeframe analiz uygulamaları.
- `appsuite/`: Tüm uygulamaları tek host altında reverse proxyleyen birleşik arayüz.
- `landing/`: Uygulama linklerini listeleyen kart tabanlı açılış sayfası.
- `calendar_md/`: ForexFactory benzeri Markdown takvimlerini JSON’a dönüştürüyor (CLI + web).
- `favicon/`: Ortak favicon, manifest ve statik varlıklar.
- `economic_calendar/`: Haber entegrasyonu için örnek JSON takvimleri.
- Kök dizinde varsayılan CSV örnekleri bulunmuyor; test için kendi verinizi eklemeniz gerekiyor.

### Ortak Modül Kalıbı
Her timeframe paketinde tipik olarak:
- `counter.py`: CSV yükleme, DC hesaplama, offset dizileri ve sinyal motoru.
- `web.py`: Çok sekmeli HTTP sunucusu; IOU/IOV formları, analiz tabloları.
- `main.py` veya ek CLI dosyaları: Converter ya da yardımcı araç giriş noktaları.

## Veri Akışı
1. Kullanıcı CSV yükler; başlıklar eş anlamlı listeleriyle normalize edilir.
2. Girdi `UTC-5` ise tüm timestamp’ler +60 dk kaydırılarak `UTC-4` normalizasyonu yapılır.
3. app48 özelinde sentetik mumlar eklenir; diğer uygulamalarda orijinal sıralama korunur.
4. Distorted Candle (DC) hesaplaması yapılır, uygulamaya özgü istisnalar uygulanır.
5. Sequence dizileri, DC olmayan mumlar üzerinden offset sürelerine bağlanır; DC kapsama kuralı gerektiğinde devreye girer.
6. OC (`Close - Open`) ve PrevOC değerleri hesaplanır, eksik veri için tahmini zaman damgaları üretilir.
7. IOU/IOV sekmeleri `limit + tolerans` eşiğini aşan kombinasyonları raporlar; News/XYZ filtresi işlenir.

## Ortak Kavramlar

### CSV Gereksinimleri
- Zorunlu sütunlar: `Time`, `Open`, `High`, `Low`, `Close (Last)` (eş anlamlılar desteklenir).
- Bozuk satırlar atlanır; veri timestamp’e göre sıralanır.
- app72 ve app80 için 2 haftalık veri beklenir; kapanış davranışları bu varsayıma göre tasarlandı.

### Sequence Dizileri
- **S1:** `1, 3, 7, 13, 21, 31, 43, 57, 73, 91, 111, 133, 157`
- **S2:** `1, 5, 9, 17, 25, 37, 49, 65, 81, 101, 121, 145, 169`

### Distorted Candle (DC)
- Varsayılan koşullar: `High ≤ prev.High`, `Low ≥ prev.Low`, `Close` önceki mumun `[Open, Close]` aralığında.
- Ardışık DC’ler engellenir; 18:00 baz mumları kural gereği DC sayılmaz.
- Uygulamaya özgü istisnalar için ilgili dosyalara bak (ör. [app80](app80.md)).
- Kapsayıcı kural: Sequence adımı DC’ye denk gelirse zaman damgası DC mumuna yazılır.
- Pozitif offsetlerde başlangıç mumu DC ise +1/+2/+3 için DC kuralı geçici olarak devre dışı bırakılır.

### Offset Sistemi
- Başlangıç noktası yakalanan ilk 18:00 mumudur.
- Offset aralığı `-3..+3`; timeframe dakikası ile çarpılarak hedef zaman elde edilir.
- Negatif offsetler yalnızca mevcut veriye dayanır; pozitif offsetlerde DC olmayan ilk mum bulunana kadar kaydırma yapılır.
- Tahmini zamanlar `pred` etiketiyle raporlanır.

### OC / PrevOC
- Her gerçek mum için `OC = Close - Open`.
- `PrevOC` bir önceki mumun OC değeridir; yoksa `-` görünür.
- Tahmin satırlarında OC ve PrevOC `-` olarak gösterilir.

### Zaman Dilimi Normalizasyonu
- Kullanıcı `UTC-4` veya `UTC-5` seçer.
- `UTC-5` seçilirse tüm kayıtlar +60 dk kaydırılıp çıktı `UTC-4` standardına getirir.

## Haber Akışı & XYZ Filtresi
- IOU formlarında “XYZ kümesi (haber filtreli)” kutucuğu etkinleştirildiğinde haber taşımayan offsetler elenir.
- `news_loader.py` JSON takvimleri farklı alanlardan (örn. `time_24h`, `time_text`) okuyup normalize eder.
- Tatil başlığı içeren olaylar `effective_news=False`; offset yalnızca bilgi amaçlı listelenir.
- All-day olaylar zaman etiketi olmadan gün bazında yakalanır, günlük not olarak görünür.
- app72’nin 16:48/18:00/19:12/20:24 slotları haber olmasa da “Kural slot HH:MM” notuyla korunur.
- Tolerans kuralları XYZ filtreyle birlikte uygulanır; limit altındaki satırlar ve tolerans içinde kalan değerler listeye girmeden elenir.

## Web Katmanı ve Yardımcı Servisler
- **landing:** Port 2000’de kart tabanlı açılış sayfası; URL parametreleriyle linkler değiştirilebilir.
- **appsuite:** Tüm backend’leri farklı path’lere proxy eder (`/app48`, `/app72`, ...); health endpoint’i `/health → ok`.
- **calendar_md:** Markdown takvimlerini JSON’a çevirerek haber entegrasyonuna veri sağlar; web arayüzünde dönüştürme formu bulunur.
- **favicon:** Ortak favicon ve manifest dosyalarını üretir.
- **economic_calendar:** Haber testleri için örnek JSON takvimleri saklar.

## Veri Setleri
- Varsayılan örnek CSV’ler depoda yer almıyor; kullanıcı kendi veri setini eklemeli.
- Test için daha önce kullanılan dosyalar: `120mdata.csv`, `test_120m.csv`, `ornek80.csv` vb. (artık depo kökünde zorunlu değil).

## Kurulum & Çalıştırma
1. Python 3.11+ kur.
2. (Opsiyonel) sanal ortam: `python3 -m venv .venv && source .venv/bin/activate`.
3. Bağımlılıklar: `pip install -r requirements.txt` (yalnızca `gunicorn`).
4. Web servislerini doğrudan modül üzerinden çalıştırabilir veya `appsuite` ile topluca başlatabilirsin.
5. Üretim senaryolarında `gunicorn` (örn. `gunicorn app120.web:main`) ve Render konfigürasyonları (`Procfile`, `render.yaml`, `railway.toml`) hazır durumda.

## Not
- Kapsamlı tarihçe ve alternatif anlatım için kökteki `agents.md` dosyasını referans olarak tutuyoruz.
