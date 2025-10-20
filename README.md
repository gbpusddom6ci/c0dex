# C0dex Çoklu Timeframe Analiz Paketi

Bu depo, **app48, app72, app80, app120 ve app321** olmak üzere beş farklı timeframe için mum verisini analiz eden, bağlı web arayüzleri ve CLI araçlarıyla birlikte çalışan bir paket içerir. Amaç; IOU/IOV sinyal taraması, distorted candle (DC) takibi, offset dizileri ve timeframe dönüştürücüleri gibi ihtiyaçları tek bir kod tabanında toplamak.

Depo aynı zamanda landing sayfası, tüm uygulamaları tek host altında birleştiren reverse proxy, ekonomik takvim dönüştürücüsü ve ortak favicon servislerini de barındırır. Bu README, projeyi devralan bir geliştiricinin başka kaynağa ihtiyaç duymadan koda hâkim olabilmesi için kapsamlı biçimde hazırlanmıştır.

---

## İçindekiler

1. [Teknoloji Yığını](#teknoloji-yığını)
2. [Dizin Yerleşimi](#dizin-yerleşimi)
3. [Uygulama Özeti](#uygulama-özeti)
4. [Veri Akışı ve Ortak Kurallar](#veri-akışı-ve-ortak-kurallar)
5. [IOV / IOU Sinyal Motoru](#iov--iou-sinyal-motoru)
6. [Haber Entegrasyonu](#haber-entegrasyonu)
7. [CLI ve Web Kullanımı](#cli-ve-web-kullanımı)
8. [Örnek Veri Setleri](#örnek-veri-setleri)
9. [Dağıtım Notları](#dağıtım-notları)
10. [Geliştirici İpuçları](#geliştirici-ipuçları)

---

## Teknoloji Yığını

- Python 3.11 (Render dağıtımı için `.python-version` ile sabitlenmiş).
- Standart kütüphane ağırlıklı kod; web katmanları `http.server` üzerine kurulu hafif HTTP servisleridir.
- Üretim ortamında `gunicorn` kullanıma hazır (`requirements.txt`).
- Pandas/Numpy bağımlılıkları yok; veri işleme el yapımı fonksiyonlarla yürür.

---

## Dizin Yerleşimi

```
app48/      48 dakikalık analiz uygulaması (CLI + web)
app72/      72 dakikalık analiz uygulaması
app80/      80 dakikalık analiz uygulaması
app120/     120 dakikalık analiz uygulaması
app321/     60 dakikalık analiz uygulaması
appsuite/   Reverse proxy ve birleşik arayüz
landing/    Basit landing sayfası
calendar_md/Markdown → JSON ekonomik takvim dönüştürücü (CLI + web)
economic_calendar/ Örnek takvim JSON dosyaları
favicon/    Ortak favicon ve manifest varlıkları
ornek/      Manuel eklenen örnek CSV veri setleri
```

Her timeframe klasörü genellikle üç modül içerir:

- `counter.py` veya `main.py`: CLI sayaç ve dönüştürücüler.
- `web.py`: Minimal web arayüzü (formlar, tablolar, çoklu dosya yükleme).
- `__init__.py`: Paket bildirimi ve paylaşılan yardımcılar.

---

## Uygulama Özeti

| Uygulama | Timeframe | Port | Dönüştürücü | Öne Çıkan Kurallar |
|----------|-----------|------|-------------|--------------------|
| app48    | 48 dk     | 2020 | 12→48       | Sentetik 18:00 & 18:48 mumları, 18:00/18:48/19:36 DC & IOU dışı |
| app72    | 72 dk     | 2172 | 12→72       | 18:00, 19:12, 20:24 DC olamaz; iki haftalık veride bu saatler ve 1. haftanın Cuma 16:48 IOU dışı |
| app80    | 80 dk     | 2180 | 20→80       | 18:00, 19:20, 20:40 ve tüm Cuma 16:40 mumları DC & IOU dışı |
| app120   | 120 dk    | 2120 | 60→120      | 18:00 DC & IOU dışı; iki Pazar hariç 20:00, tüm Cuma 16:00 DC & IOU dışı |
| app321   | 60 dk     | 2019 | —           | Pazar dışı 20:00 DC dışı; 18:00/19:00/20:00 IOU dışı |
| appsuite | —         | 2100 | —           | Tüm uygulamaları tek hostta reverse proxy olarak sunar |
| landing  | —         | 2000 | —           | Tanıtım kartları ve linkler |

Tüm web arayüzleri IOU/IOV sekmeleri dahil çoklu CSV yükler, sonuçları dosya kartı olarak raporlar ve haber entegrasyonundan gelen etiketleri gösterir.

---

## Veri Akışı ve Ortak Kurallar

1. **CSV okuma:** `Time`, `Open`, `High`, `Low`, `Close (Last)` başlıkları (eş anlamlılar desteklenir) normalize edilir. Bozuk satırlar atlanır, veri timestamp’e göre sıralanır.
2. **Zaman dilimi:** Girdi `UTC-5` ise tüm timestamp’ler +60 dk kaydırılarak `UTC-4` bazına alınır.
3. **Sentetik mumlar:** Yalnızca app48, her gün (ilk gün hariç) 18:00 ve 18:48 mumlarını sentetik olarak üretir.
4. **DC hesaplama:** `compute_dc_flags` distorted candle’ları işaretler; ardışık DC engellenir, 18:00 sonra gelen pozitif offset adımları DC olmayan muma kayar.
5. **Sequence hizalama:** `S1` (1,3,7,...) ve `S2` (1,5,9,...) dizileri desteklenir. Pozitif offsetlerde DC kapsayıcı kuralı ve “DC olmayan muma ilerle” mantığı devrededir.
6. **OC / PrevOC:** `OC = Close - Open`, `PrevOC` önceki mumun OC’sidir. Tahmini satırlarda `-` ile gösterilir.
7. **Offset sistemi:** 18:00 baz mumu referans alınır, offset değerleri `-3..+3` arasıdır. Veri yoksa tahmini timestamp `pred` etiketiyle raporlanır.

### Distorted Candle (DC) İstisnaları

- Genel kural: `High ≤ prev.High`, `Low ≥ prev.Low`, `Close` önceki mumun `[Open, Close]` aralığında ise mum DC sayılır; ardışık DC’ye izin verilmez.
- Varsayılan: 18:00 mumu hiçbir zaman DC olmaz.
- **app48:** 13:12–19:36 arası DC sayılmaz (normal mum). Sentetik 18:00, 18:48 mumları DC kapsamı dışında tutulur.
- **app72:** 18:00, 19:12, 20:24 ve Cuma 16:00 DC olamaz.
- **app80:** 18:00, 19:20, 20:40 ve tüm Cuma 16:40 DC olamaz.
- **app120:** 18:00 ve Cuma 16:00 DC değildir; iki Pazar dışındaki 20:00 mumları da DC sayılmaz.
- **app321:** Pazar hariç 20:00 DC değildir; ayrıca 13:00–20:00 arası DC normal mum kabul edilir.

### IOU Kısıtları

Her uygulama kendi IOU sekmesinde aşağıdaki özel saatleri tamamen hariç tutar:

- **app48:** 18:00, 18:48, 19:36.
- **app72:** 18:00, 19:12, 20:24 ve iki haftalık verinin ilk haftası Cuma 16:48.
- **app80:** 18:00, 19:20, 20:40 ve tüm Cuma 16:40.
- **app120:** 18:00, iki Pazar dışındaki 20:00 ve tüm Cuma 16:00.
- **app321:** 18:00, 19:00 ve 20:00.

Tüm IOU taramaları limit ve isteğe bağlı `± tolerans` alanını kullanır; `|OC|` ve `|PrevOC|` değerleri `limit + tolerans` eşiğini aşmayan satırlar rapordan düşer.

---

## IOV / IOU Sinyal Motoru

Sinyal mantığı CLI ve web katmanlarında ortaktır:

1. CSV yüklenir, timezone uyarlanır, DC istisnaları uygulanır.
2. İlk 18:00 mumu bulunur, offset hizalamaları DC olmayan mumlara kaydırılır.
3. Sequence hücreleri DC kapsayıcı kuralıyla hesaplanır.
4. `OC` ve `PrevOC` değerleri üzerinden sinyal filtresi çalışır:
   - IOV: Zıt işaretli ikililer ve limit kontrolleri.
   - IOU: Aynı işaretli ikililer ve limit + tolerans kontrolleri.
5. Sonuçlar offset bazlı kartlarda listelenir; sentetik/gerçek mum etiketi (`syn/real`) ve DC kapsaması `(rule)` şeklinde görünür.
6. Haber entegrasyonu varsa kart başlığına `XYZ Kümesi` notu düşer ve haber sütunu kategori/bilgi etiketleri ile doldurulur.

Varsayılan limitler pozitif kabul edilir; negatif girilirse mutlak değeri alınır. Limit `0` ise yalnızca `OC ≠ 0` olan satırlar eşiği geçer.

---

## Haber Entegrasyonu

Tüm IOU sekmeleri economic_calendar klasöründeki JSON takvimlerini kullanarak haberleri gösterir. `news_loader.py` zaman ve tarih eşlemesini yapar:

- **JSON şeması:** `date`, `time`/`time_24h`, `title`, `currency`, `impact`, `all_day`, `recent_null`, `actual`, `forecast`, `previous` gibi alanları destekler.
- **Kategori belirleme:**
  - `Holiday`: Başlıkta “holiday” geçen ve all-day + null değerli kayıtlar.
  - `AllDay`: All-day olup holiday olmayan, genelde OPEC veya German Prelim CPI gibi gün boyu etkinlikler.
  - `Speech`: Saat içeren ve `actual` değeri `null` olan konuşmalar.
  - `Data`: Diğer tüm veri odaklı olaylar.
- Bu etiketler sadece bilgilendirme amaçlıdır; **Holiday** ve **AllDay** kayıtları XYZ filtresini elemez.
- `recent_null=true` olan öğeler `(null)` ekiyle gösterilir; veri açıklaması henüz paylaşılmamış olaylardır.
- app72 için 16:48/18:00/19:12/20:24 slotları haber olmasa bile “Kural slot” etiketiyle korunur.

XYZ filtresi: Haber veya özel slot bulunmayan offsetler elenir; holiday ve all-day olayları “bilgilendir” modunda kalır, eleme tetiklemez.

---

## CLI ve Web Kullanımı

Aşağıdaki örnekler sanal ortam (opsiyonel) aktifken çalıştırılabilir:

```bash
# Sanal ortam (isteğe bağlı)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Web Arayüzleri

```bash
python3 -m landing.web --host 0.0.0.0 --port 2000
python3 -m appsuite.web --host 0.0.0.0 --port 2100
python3 -m app48.web   --host 0.0.0.0 --port 2020
python3 -m app72.web   --host 0.0.0.0 --port 2172
python3 -m app80.web   --host 0.0.0.0 --port 2180
python3 -m app120.web  --host 0.0.0.0 --port 2120
python3 -m app321.web  --host 0.0.0.0 --port 2019
python3 -m calendar_md.web --host 0.0.0.0 --port 2300
```

Web formları birden fazla CSV dosyasını aynı anda yükler; sonuçlar dosya kartlarında ayrıştırılır. IOU sekmeleri limit ve tolerans alanlarını içerir, XYZ filtresi isteğe bağlıdır.

### CLI Örnekleri

```bash
# 120 dakikalık analiz (sequence S2, +1 offset, DC göster)
python3 -m app120.counter --csv path/to/data.csv --sequence S2 --offset +1 --show-dc

# 60→120 dönüştürücü
python3 -m app120 --csv path/to/60m.csv --input-tz UTC-5 --output out-120m.csv

# app80 IOU taraması (limit 0.08, tolerans 0.005)
python3 -m app80.counter --csv path/to/data.csv --sequence S1 --scan-iou --limit 0.08 --tolerance 0.005

# app48 tahmin modu
python3 -m app48.main --csv path/to/data.csv --predict 49

# Ekonomik takvimi markdown'dan dönüştür
python3 -m calendar_md --input takvim.md --output economic_calendar/takvim.json --year 2025
```

Tüm CLI araçlarında `--help` parametresi detaylı argüman listesini gösterir.

---

## Örnek Veri Setleri

Depoda otomatize test verileri bulunmadığından `ornek/` klasörüne manuel CSV’ler eklenmiştir. Her timeframe için “gerçek dünya” örnekleri içerir; IOU/IOV kurallarını ve haber etiketlerini hızlıca doğrulamak için kullanılabilir. Kullanıcı kendi veri setlerini bu klasöre kopyalayabilir; repo bu dosyaları sürüm kontrolüne dahil edecek şekilde yapılandırılmıştır.

---

## Dağıtım Notları

- Render için `render.yaml` ve `Procfile` örnek komutları sağlar.
- Railway/Nixpacks dağıtımı için `railway.toml` bulunur.
- `Dockerfile`, minimal Python tabanlı container’da web servislerini başlatmaya uygundur.
- Üretim dağıtımlarında her servis için farklı port seçmek gerekir; `appsuite` reverse proxy’si tek host altında bu servisleri yayınlamak için önerilen yöntemdir.

---

## Geliştirici İpuçları

- `python3 -m compileall .` komutu ile sözdizimi kontrolü yapabilirsiniz; unittest/pytest entegrasyonu yoktur.
- IOU/IOV davranışlarını doğrulamak için web arayüzlerindeki çoklu dosya yükleme özelliği pratik bir yol sağlar.
- Yeni timeframe eklemek için en güncel referans `app120`’dir; DC ve IOU mantığını paylaşan yardımcı fonksiyonları tekrar kullanmaya çalışın.
- `__pycache__` klasörleri sürüm takibinde tutulmaz; gerektiğinde manuel temizlenebilir.
- Haber JSON’larını düzenli güncellemek gerekir. “Null” actual değerleri konuşma/speech kategorisine, all day + null kombinasyonu all-day kategorisine düşer; kategoriler sadece görsel bilgilendirme amaçlıdır.

Projeyi devralanların ilk iş olarak `landing.web` veya `appsuite.web` ile tüm arayüzleri ayağa kaldırıp `ornek/` altındaki dosyalarla IOU kartlarını incelemesi önerilir. Bu sayede DC istisnaları, haber etiketleri ve tolerans parametrelerinin pratikte nasıl çalıştığı kolayca anlaşılır.
