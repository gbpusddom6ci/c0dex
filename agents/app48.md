# app48 — 48 Dakikalık Analiz

## Genel İşleyiş
- 48 dakikalık timeframe’de sequence sayımı yaparken sentetik mumlar ekleyerek piyasa kapanış aralığını dengeler.
- Web arayüzü port 2020’de çalışır; Analiz, DC List, Matrix, IOU Tarama ve sentetik yapı hakkında özetler içerir.
- Kullanıcı verisi `UTC-4` veya `UTC-5` olabilir; `UTC-5` seçildiğinde tüm kayıtlar +60 dk kaydırılarak normalize edilir.

## Modül Yapısı
- `main.py`: CSV yükleme, sentetik mum ekleme, DC hesaplama, offset akışı ve IOU tarama fonksiyonlarını barındırır.
- `web.py`: Çoklu dosya yükleme, limit/tolerans ayarları, haber filtresi ve sonuç kartlarını yönetir.
- `app48_dc`: Sentetik/gerçek ayrımını `tag=syn` etiketiyle gösteren yardımcı DC listesi üretir.

## Sentetik Mum Mantığı
- İlk gün hariç, her gün 18:00 ve 18:48 saatlerinde sentetik mum üretilir.
- 17:12 ile 19:36 arasına eklenen sentetik mumların:
  - `open` değeri önceki mumun `close`u ile eşitlenir.
  - `close` değeri bir sonraki mumun `open`una lineer şekilde yaklaşacak biçimde belirlenir.
  - `high` ve `low` değerleri, çevresindeki mumların min/max değerleri üzerinden seçilir.
- Sentetik mumlar analiz çıktılarında `syn` etiketiyle ayrışır; DC değerlendirmesi gerçek mumlarla tutarlı şekilde yapılır.

## DC ve Offset Kuralları
- Standart DC koşulları geçerlidir; ardışık DC engellenir, 18:00 baz mumları DC sayılmaz.
- Özel istisna: Pazartesi–Cumartesi 13:12 ile 19:36 (19:36 dahil) arasındaki mumlar DC sayılmaz; buna 18:00, 18:48 ve 19:36 slotları da dahildir.
- Sentetik saatlerde DC değerlendirmesi istisnayı koruyacak şekilde yapılır; offset sütunları aynı zaman damgasına sabitlenmez.
- Pozitif offsetler DC olmayan ilk gerçek muma ilerleyerek hesaplanır; kapsayıcı kural sequence zamanını DC mumuna sabitleyebilir.

## IOU Tarama
- Aynı işaretli OC/PrevOC çiftlerini raporlar.
- Limit alanı mutlak alınır; hem `|OC|` hem `|PrevOC|` `limit + tolerans` eşiğini aşmadıkça satırlar listelenmez.
- Varsayılan tolerans 0.005’tir; form alanı üzerinden değiştirilebilir.
- Sonuçlar sentetik (`syn`) veya gerçek (`real`) etiketleriyle kartlarda gösterilir.
- XYZ filtresi aktifken haber taşımayan offsetler elenir; tatil satırları bilgi amaçlı listelenir.
- 18:00, 18:48 ve 19:36 zaman damgalarına sahip hiç bir mum IOU olarak raporlanmaz.

## Analiz ve Matrix Sekmeleri
- Analiz sekmesi sequence akışını, DC durumlarını, OC/PrevOC değerlerini ve tahmini slotları sunar.
- Matrix sekmesi `-3..+3` offsetlerini tek tabloda özetler; tahmini zamanlar `pred` etiketiyle görünür.
- DC List sekmesi sentetik ve gerçek mumları ayırt ederek ham OHLC verisini gösterir.

## Veri Hazırlama Notları
- CSV sütun başlıkları esnektir; `Close (Last)` gibi varyantlar otomatik tanınır.
- Sentetik mumların doğru eklenebilmesi için veri kronolojik sırada olmalı ve günler arası boşluklar doğal akışta bırakılmalıdır.
- Limit/tolerans ve timezone ayarları çoklu dosya yüklemelerinde tüm dosyalara aynı şekilde uygulanır.
