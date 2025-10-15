# app72 — 72 Dakikalık Analiz

## Genel İşleyiş
- 12 dakikalık mumlardan 72 dakikalık bloklar oluşturup sequence sayımı, DC analizi ve IOU sinyal taraması yapar.
- Web arayüzü varsayılan port 2172’de çalışır; Analiz, DC List, Matrix, IOU Tarama ve 12→72 Converter sekmelerini içerir.
- Sistem iki haftalık veri akışı varsayımıyla çalışır; hafta sonu boşlukları dönüştürücü ve analiz aşamalarında dikkate alınır.

## Modül Yapısı
- `counter.py`: CSV işleme, DC hesaplama, offset eşlemesi, OC/PrevOC üretimi ve IOU sinyal motoru.
- `main.py`: Yedi adet 12 dakikalık mumdan bir 72 dakikalık mum üretir; Pazar 18:00 öncesi ve Cumartesi mumları otomatik olarak atlanır.
- `web.py`: Çoklu dosya yükleme, limit/tolerans ayarları, XYZ filtresi ve haber entegrasyonunu yönetir.

## DC ve Offset Kuralları
- Standart DC kriterleri geçerlidir; ardışık DC engellenir.
- Özel istisnalar:
  - 18:00 mumları (Pazar dahil) DC olamaz.
  - Cuma 16:48, Cuma 16:00 (hafta kapanışı), Pazar hariç 19:12 ve 20:24 mumları DC olamaz.
- Pozitif offsetler DC olmayan ilk gerçek muma kaydırılır; kapsayıcı kural sequence zaman damgasını DC mumuna sabitleyebilir.
- Negatif offsetlerde veriye göre tahmin üretilir; 18:00 öncesi boşluklar tahmin satırı oluşturabilir.

## IOU Tarama
- Aynı işaretli OC/PrevOC çiftlerini aranır; limit alanı mutlak değer olarak alınır.
- Hem `|OC|` hem `|PrevOC|` değerleri `limit + tolerans` eşiğini aşmadıkça satırlar raporlanmaz. Varsayılan tolerans 0.005, form üzerinden değiştirilebilir.
- Çoklu CSV yükleme desteklenir; sonuçlar dosya bazlı kartlarda gösterilir.
- XYZ filtresi aktifken haber taşımayan offsetler elenir. `news_loader.py`’nin sağladığı haber sütunu `Var`, `Holiday`, `Yok` etiketlerini içerir.
- **Özel slot kuralı:** 16:48, 18:00, 19:12 ve 20:24 zaman damgaları haber olmasa bile “Kural slot HH:MM” notuyla korunur; XYZ filtresi bu satırları elemez.

## Converter ve Matrix Sekmeleri
- 12→72 Converter sekmesi haftasonu boşluklarını otomatik atlayarak blokları oluşturur; çıktı CSV olarak indirilebilir.
- Analiz sekmesi sequence indekslerine göre zaman damgalarını, DC durumlarını, OC/PrevOC değerlerini ve tahmin satırlarını listeler.
- Matrix sekmesi `-3..+3` offsetlerinin tamamını tek tabloda sunar; tahmin satırları `pred` etiketiyle görünür.
- DC List sekmesi gerçek mumların OHLC değerlerini ve DC bayraklarını detaylı biçimde gösterir.

## Veri Hazırlama Notları
- Veri 12 dakikalık eşit aralıklarda ve iki haftalık dönemi kapsayacak şekilde hazırlanmalıdır.
- Haftasonu boşlukları (Cumartesi/Pazar) dönüştürücü tarafından otomatik atlanır; bu sayede sequence ilerleyişi beklenen zamanlamaya oturur.
- Limit ve tolerans ayarları tüm yüklenen dosyalar için aynı anda uygulanır; hassasiyet gerektiren senaryolarda tolerans düşürülebilir veya artırılabilir.
