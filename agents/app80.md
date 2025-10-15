# app80 — 80 Dakikalık Analiz

## Genel İşleyiş
- 20 dakikalık mum gruplarından 80 dakikalık bloklar üretip analiz eder.
- Web arayüzü varsayılan olarak port 2180’de çalışır; Analiz, DC List, Matrix, IOU Tarama ve 20→80 Converter sekmeleri içerir.
- Veri girişi `UTC-4` veya `UTC-5` olabilir; `UTC-5` seçildiğinde tüm kayıtlar +60 dk kaydırılır.
- Sistem, iki haftalık veri aralığına göre tasarlanmıştır. İlk haftanın Cuma günündeki 16:40 mumu hafta kapanışı olarak kabul edilir.

## Modül Yapısı
- `counter.py`: CSV yükleme, DC hesaplama, offset sıralaması, OC/PrevOC üretimi ve IOU sinyal taraması.
- `main.py`: Dört adet 20 dakikalık mumdan tek 80 dakikalık mum oluşturur (open=ilk open, close=son close, high/low=blok içi max/min).
- `web.py`: Çok sekmeli HTTP sunucusu; çoklu dosya yükleme, haber filtresi ve tolerans ayarlarını yönetir.

## DC ve Offset Kuralları
- Standart DC kriterleri geçerlidir; 18:00 baz mumları DC sayılmaz.
- Özel istisnalar:
  - (Pazar hariç) 18:00, 19:20, 20:40 mumları DC olamaz.
  - Cuma 16:40 mumları (ilk hafta dahil) DC olamaz; hafta kapanışı bu muma sabitlenmiştir.
- Pozitif offsetler DC olmayan ilk gerçek muma kaydırılarak hesaplanır; DC kapsayıcı kuralı gerektiğinde devreye girer.
- Negatif offsetlerde tahmin mekanizması verinin mevcut kısmına dayanır; 18:00 öncesi için ekstra işleme gerek yoktur.

## IOU Tarama
- Aynı işaretli OC/PrevOC kombinasyonlarını arar.
- Limit alanına girilen değer mutlak alınır; tolerans ile birlikte değerlendirilir.
- Sonuçların görünmesi için hem `|OC|` hem de `|PrevOC|` değerlerinin `limit + tolerans` eşiğini aşması gerekir. Varsayılan tolerans 0.005’tir ancak form alanından değiştirilebilir.
- Birden fazla CSV aynı anda yüklenebilir; her dosya için sequence, limit, tolerans ve timezone parametreleri ortak kullanılır. Sonuçlar dosya başına kartlarda gösterilir.
- XYZ filtresi aktifken haber taşımayan offsetler elenir. Haber tablosu `Var`, `Holiday` veya `Yok` etiketleriyle gösterilir.

## Matrix ve Analiz Sekmeleri
- Analiz sekmesi sequence indekslerine göre zaman damgalarını, DC durumunu, OC/PrevOC değerlerini ve tahmini slotları listeler.
- Matrix sekmesi `-3..+3` offsetlerinin tamamını tek tabloda sunar; tahmini slotlar `pred` etiketiyle işaretlenir.
- DC List sekmesi gerçek mumların OHLC değerlerini ve DC bayraklarını ham halde gösterir.

## Veri Hazırlama Notları
- Dönüştürücü sekmesi 20 dakikalık veriyi 80 dakikalık bloklara çevirir; haftasonu/döngü boşlukları doğal sıralamaya göre ele alınır.
- Cuma 16:40 kapanış kuralı nedeniyle veri setinin iki hafta süreyle ve ardışık günleri kapsaması beklenir; boşluklar tahmin satırlarını tetikleyebilir.
- OC/PrevOC hesapları yalnızca gerçek mumlar üzerinden yapılır; sentetik veya doldurulmuş değer kullanılmaz.
