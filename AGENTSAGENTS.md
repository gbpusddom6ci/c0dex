# AGENTS – Geliştirici ve Yapay Zekâ Rehberi (Yeni Öneri)

Bu belge, depodaki tüm timeframe uygulamalarını, web katmanlarını ve veri kurallarını tek yerde ve yüksek doğrulukla özetler. Amaç, yeni devralan bir geliştiricinin veya bir yapay zekâ ajanın kod tabanını başka kaynağa ihtiyaç duymadan anlayıp güvenle çalışabilmesidir.

Not: Bu dosya mevcut `agents.md` yerine geçmesi amacıyla hazırlanmış bir taslaktır. Beğenirseniz kökteki `agents.md` ile değiştirilebilir.

## 1) Amaç ve Kapsam
- Kapsam: Depo kökü ve tüm alt dizinler. Daha derine yerleştirilecek olası yerel AGENTS dosyaları, bulunduğu klasör ağacında daha yüksek önceliğe sahiptir.
- Hedefler:
  - Uygulama mimarisini ve ortak kavramları tek bakışta kavratmak.
  - IOU/IOV sinyal motoru, DC kuralları, offset hizalama ve tolerans mantığını netleştirmek.
  - Çalıştırma, dağıtım ve katkı yönergelerini sade ama eyleme dönük şekilde sunmak.
- İlkeler:
  - Python 3.11+, standart kütüphane ağırlıklı yaklaşım; dış bağımlılıklar minimal (varsayılan sadece `gunicorn`).
  - Var olan davranışı koru; köklü değişikliklerde önce küçük, doğrulanabilir adımlar.

## 2) En Güncel Özellikler (Özet)
- 2025-10 – IOU tolerans parametresi: `± tolerans` (varsayılan 0.005); sinyaller yalnızca `|OC|` ve `|PrevOC| ≥ limit + tolerans` ise listelenir.
- 2025-09 – IOU XYZ filtresi & haber entegrasyonu: Tatiller, all-day haberler ve app72’nin 16:48/18:00/19:12/20:24 slotları özel ele alınır.
- 2025-08 – IOU/IOV sinyal motoru: IOU (aynı işaret) ve IOV (zıt işaret) taramaları ortak akışla.
- 2025-08 – Çoklu CSV yükleme: app48/app72/app80/app120/app321 IOU; app120 IOV çoklu yükleme destekler.
- 2025-07 – app120 birleşik web arayüzü: Analiz, DC List, Matrix, IOV, IOU, 60→120 Converter tek UI.
- 2025-06 – app80 & app72 converter’ları: 20→80 ve 12→72 web + CLI.
- 2025-05 – app48 sentetik mumlar: 18:00 ve 18:48 sentetik slotlar.
- Eski çekirdek: app321 (60m) sayım, DC, matrix ve tahmin.

## 3) Mimari Genel Bakış
- Teknoloji:
  - Python 3.11+, minimal HTTP (`http.server`), CLI modülleri (`-m package.module`).
  - Prod: `gunicorn` ile çalıştırma. Pandas/Numpy opsiyonel ve varsayılan olarak kullanılmaz.
- Dizin Yapısı (güncel):
  - `app120/`, `app80/`, `app72/`, `app48/`, `app321/`, `app90/`, `app96/` – timeframe uygulamaları (CLI + web).
  - `appsuite/` – Reverse proxy ile tüm uygulamaları tek host altında toplar.
  - `landing/` – Basit tanıtım sayfası, uygulama linklerini listeler.
  - `calendar_md/` – Markdown ekonomi takvimlerini JSON’a çeviren araç (CLI + web).
  - `favicon/` – Ortak favicon/manifest varlıkları.
  - `economic_calendar/` – Haber entegrasyonu için örnek JSON takvimleri.
  - Kök: `Procfile`, `render.yaml`, `railway.toml`, `Dockerfile`, `.python-version`, `requirements.txt`.

## 4) Ortak Kavramlar ve Kurallar
- CSV Formatı
  - Zorunlu sütunlar: `Time`, `Open`, `High`, `Low`, `Close (Last)` (eş anlamlılar desteklenir).
  - Bozuk satırlar atlanır; veri timestamp’e göre sıralanır.
  - `UTC-5` girdisi seçilirse tüm timestamp’ler +60 dk kaydırılarak çıktı `UTC-4`’e normalize edilir.
- Sequence Dizileri
  - S1: 1, 3, 7, 13, 21, 31, 43, 57, 73, 91, 111, 133, 157
  - S2: 1, 5, 9, 17, 25, 37, 49, 65, 81, 101, 121, 145, 169
- Distorted Candle (DC)
  - DC koşulu: `High ≤ prev.High`, `Low ≥ prev.Low`, `Close` ∈ `[prev.Open, prev.Close]`.
  - Ardışık DC engellenir; varsayılan olarak 18:00 DC sayılmaz.
  - Kapsayıcı kural: Sequence adımı DC’ye denk gelirse zaman damgası o DC mumuna yazılır.
  - Pozitif Offset İstisnası: +1/+2/+3 başlangıç mumu DC ise ilgili offset için normal kabul edilir (DC kuralı geçici devre dışı).
- Offset Sistemi
  - Başlangıç: ilk 18:00 mumu.
  - Değer aralığı: `-3..+3`; timeframe dakika değeriyle çarpılarak hedef zaman belirlenir.
  - Pozitif offsetler “ilk DC olmayan muma” kaydırılır; negatif offset tahmin gerektirmez (18:00 öncesi veri yoksa).
  - Tahmini zamanlar `pred` etiketiyle raporlanır. Hafta sonu atlama kuralları timeframe’e göre uygulanır.
- OC/PrevOC
  - `OC = Close - Open`; `PrevOC = prev.OC`; tahmini satırlarda `OC=- PrevOC=-`.
- Zaman Dilimi
  - Girdi: `UTC-4` veya `UTC-5`. Çıktı normalize: `UTC-4`.

### Uygulama Bazlı DC/Slot İstisnaları (özet)
- app321 (60m): 13:00–20:00 arası DC’ler normal mum; 20:00 (Pazar hariç) asla DC sayılmaz. IOU listesinde 18:00, 19:00, 20:00 raporlanmaz.
- app48 (48m): 13:12–19:36 arası DC’ler normal kabul. Sentetik slotlar: 18:00 ve 18:48; IOU listesinde 18:00, 18:48, 19:36 raporlanmaz.
- app72 (72m): 18:00 (Pazar dahil), Cuma 16:48; (Pazar hariç) 19:12, 20:24; Cuma 16:00 DC olamaz.
- app80 (80m): (Pazar hariç) 18:00, 19:20, 20:40; Cuma 16:40 DC olamaz.
- app120 (120m): Genel kapsayıcı kural dışında özel istisna yok; Cuma 16:00 hafta kapanışı DC sayılmaz.
- app90 (90m): 18:00, (Pazar hariç) 19:30 ve Cuma 16:30 DC değildir; aynı slotlar IOU taramasında dışlanır.
- app96 (96m): 18:00, (Pazar hariç) 19:36 ve Cuma 16:24 DC değildir; aynı slotlar IOU taramasında dışlanır.

## 5) IOU/IOV Sinyal Motoru
- Limit & Tolerans
  - Limit mutlak değerdir; negatif girilirse `abs(limit)` alınır. `limit=0` yalnızca sıfır olmayan değerleri eşik üstü kabul eder.
  - Tolerans `±` alanıdır; eşik: `abs(x) ≥ limit + tolerans`. Varsayılan tolerans 0.005.
  - Sonuç tablolarında `limit + tolerans` eşiğini aşmayan satırlar elenir (tolerans bandı dahil).
- IOU Algoritması (kısa akış)
  1) CSV hazırlanır, zaman normalize edilir, `compute_dc_flags` ile DC istisnaları işaretlenir.
  2) `find_start_index` ile ilk 18:00 bulunur; `compute_offset_alignment` ile pozitif offsetler DC olmayan mumlara hizalanır.
  3) Seçilen sequence boyunca hücreler için mum index’i atanır; DC kapsaması gerekiyorsa `(rule)` etiketiyle belirtilir.
  4) Sinyal filtresi: `oc = close-open`, `prev_oc = prev.close-prev.open`. IOU için işaretler aynı olmalı ve her ikisi de `≥ limit+tolerans`.
  5) Sonuçlar offset/dosya bazında gruplanır; sentetik/gerçek `syn/real`, haberler `news_loader` çıktılarıyla zenginleştirilir.
- IOV (app120): IOU ile aynı arayüz; işaretler zıt olmalıdır. S1’de `1,3`; S2’de `1,5` sinyal dışıdır.
- IOU Slot Dışlamaları (hatırlatma): app321 (18/19/20), app48 (18:00/18:48/19:36), app90 (18:00/19:30/16:30 Cuma), app96 (18:00/19:36/16:24 Cuma).

## 6) Uygulama Paketleri (Özet Kartlar)
- app120 (port 2120)
  - Modüller: `counter.py` (120m sayım/DC), `web.py` (6 sekme), `main.py` (60→120 converter).
  - Web sekmeleri: Analiz, DC List, Matrix, IOV Tarama (multi), IOU Tarama (multi, tolerans), 60→120 Converter.
  - CLI örnekleri: `python3 -m app120.counter --csv data.csv --sequence S2 --offset +1 --show-dc` | `python3 -m app120 --csv in60.csv --input-tz UTC-5 --output out120.csv`.
- app80 (port 2180)
  - Modüller: `counter.py`, `web.py`, `main.py` (20→80).
  - Web sekmeleri: Analiz, DC List, Matrix, IOU Tarama, 20→80 Converter.
- app72 (port 2172)
  - Modüller: `counter.py`, `web.py`, `main.py` (12→72).
  - Web sekmeleri: Analiz, DC List, Matrix, IOU Tarama, 12→72 Converter.
- app48 (port 2020)
  - Özellik: Sentetik mum ekleme (her gün 18:00 ve 18:48, ilk gün hariç). IOU’da 18:00/18:48/19:36 raporlanmaz.
  - Modüller: `main.py` (sayım + converter), `web.py`.
- app321 (port 2019)
  - Web sekmeleri: Analiz, DC List, Matrix, IOU Tarama (multi, tolerans, 18/19/20 dışı).
  - CLI: `python3 -m app321.main --csv data.csv --sequence S1 --offset -3 --show-dc`.
- app90 (port 2190)
  - Modüller: `counter.py`, `web.py`, `main.py` (30→90). IOU’da 18:00/19:30/16:30(Cum) dışı.
- app96 (port 2196)
  - Modüller: `counter.py`, `web.py`, `main.py` (12→96). IOU’da 18:00/19:36/16:24(Cum) dışı.

## 7) Web Katmanı ve Birleşik Arayüzler
- landing (`python3 -m landing.web --port 2000`): Kart tabanlı açılış; linkler parametreyle değiştirilebilir.
- appsuite: Reverse proxy; tüm backend’ler ayrı thread’de başlar, linkler proxy prefix’ine göre rewrite edilir. Health: `/health → ok`.
- calendar_md (`python3 -m calendar_md.web --port 2300`): Çoklu `.md` yükler; ayrı JSON’lar üretir ve zip indirir. CLI: `python3 -m calendar_md --input takvim.md --output out.json --year 2025`.
- Haber/XYZ filtresi
  - `news_loader.py` JSON şeması alanları: `date`, `time`/`time_24h` (opsiyonel), `title`, `session`, `currency`, `impact`, `all_day`, `recent_null`, `actual`, `previous`, `forecast`.
  - “holiday” geçen başlıklar `effective_news=False` (yalnız bilgi); tatil satırları grafiksel görünür ama XYZ kümesi dışında kalır.
  - All-day olaylar gün bazında yazılır; tatil değilse slot korunabilir (app72 özel slotları hariç kural notlarına bakınız).

## 8) Çalıştırma ve Dağıtım
- Kurulum
  - Python 3.11+, opsiyonel sanal ortam: `python3 -m venv .venv && source .venv/bin/activate`.
  - Bağımlılık: `pip install -r requirements.txt`.
- Hızlı başlatmalar (lokal)
  - IOU/Analiz örneği: `python3 -m app120.web --host 0.0.0.0 --port 2120`.
  - Birleşik proxy: `python3 -m appsuite.web --host 0.0.0.0 --port 2001` (varsa ilgili entrypoint’e bakın).
- Prod ipuçları
  - `gunicorn app120.web:main` benzeri komutlar; Render için `Procfile`/`render.yaml`, Railway için `railway.toml` hazır.

## 9) Geliştirici Rehberi (Stil ve Çalışma İlkeleri)
- Kapsam & Öncelik
  - Bu rehber kök için geçerlidir. Daha derindeki AGENTS dosyaları çatışırsa en derindeki kazanır.
- Değişiklik İlkeleri
  - Davranışı değiştirmeyin; önce mevcut test/örnek akışlarıyla doğrulayın.
  - DC/offset/IOU kurallarını merkezi fonksiyonlarda tutarlı uygulayın; istisnalar yalnız uygulama katında.
  - Çoklu CSV iş akışlarını ve tolerans mantığını bozmamaya özen gösterin.
- Kod Stili
  - Standart kütüphane ağırlıklı, sade fonksiyonlar; isimler anlamlı, bir harfli değişkenlerden kaçının.
  - Dosya düzeni mevcut kalıplara paralel: `counter.py`, `web.py`, `main.py`.
  - Dış lib eklemeyin (gerekliyse net gerekçe ve küçük kapsam).
- Web/UI
  - Minimal HTML; form alanları: sequence seçimi, limit, `± tolerans`, timezone, çoklu dosya.
  - IOU/IOV tablolarında `syn/real`, `(rule)` ve haber etiketlerini koruyun.
- Doğrulama
  - Küçük veri setleriyle CLI ve web uçlarını deneyin. Haber filtreli/filtre siz sonuçları kıyaslayın.
  - Büyük dosyalarda tarayıcı POST limitlerini gözetin; boyut kontrolü/makul sınırlar.

## 10) Sık Karşılaşılan Notlar
- Çoklu yükleme: 25 adede kadar CSV tek formda işlenebilir.
- Limit seçimi: `limit=0` pratikte sinyalleri aşırı geniş/boş hale getirebilir; tipik değerler > 0.
- `± tolerans`: Eşiğe eklenir; büyütmek raporu daraltır.
- Zaman dilimi: `UTC-5` girdi +1 saat kaydırılır, çıktı `UTC-4`.
- Sentetik mumlar: app48 sonuçlarında normal count’a dahil; DC listesinde `tag=syn` ile ayrışır.

---
Bu belge bilinçli olarak az sayıda kod bloğu içerir ve asıl odak doğruluk + eyleme geçirilebilirliktir. Kodla uyuşmazlık görülürse kaynak kod her zaman son kaynaktır; yine de bulgularınızı bu dosyaya taşıyarak güncel tutmanız tavsiye edilir.

