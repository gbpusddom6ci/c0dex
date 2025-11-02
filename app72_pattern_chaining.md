# app72 IOU Pattern Zinciri Notları

Bu doküman, 2024-xx tarihli güncelleme ile `app72` IOU arayüzünde yapılan örüntü (pattern) değişikliklerini özetler. Amaç, ardışık IOU analizleri sırasında oluşan pattern listelerini tek bir zincir üzerinde birleştirme mantığını ve arayüzdeki yeni davranışı kaydetmektir.

## 1. Genel Bakış

1. İlk IOU çalıştırması sırasında her dosya için bağımsız pattern listeleri üretilmeye devam eder.
2. Ardından gelen IOU çalıştırmaları, önceki sonuçların pattern listesini base64/JSON olarak form içinde saklar (`previous_pattern_payload`).
3. Yeni sonuçlar üretildiğinde, mevcut pattern seti bu geçmişe eklenir; sonrasında tüm pattern setleri art arda zincirlenir.
4. Zincirleme işlemi, pattern durum makinesinin kurallarını (±1/±3 üçlüleri, ±2 girişleri, 0 dönüşleri vb.) koruyarak tüm olası devam yollarını üretir.

## 2. Teknik Değişiklikler

- `render_combined_pattern_panel` fonksiyonu eklendi. Bu fonksiyon:
  - Zincirlenmiş pattern dizilerini tek listeye dönüştürür (`build_chained_pattern_sequences`).
  - Başlangıç değerine göre sonuçları gruplar ve her grup için `<details>` başlığı açar.
  - Görsel tutarlılık için `render_pattern_panel` ile aynı token stili, renklendirme ve tooltip mantığını kullanır.
  - Birleşik listede dosya isimleri ve joker işaretleri korunur.
- Pattern geçmişi artık meta bilgiler (dosya adları + joker indeksleri) ile saklanıyor. Böylece zincirlenen setler, hangi dosya/Joker kombinasyonundan geldiğini biliyor.
- Formun hidden alanları:
  - `previous_results_html`: Stacked HTML sonuçları taşır.
  - `previous_pattern_payload`: `groups`, `meta` ve `allow_zero_after_start` anahtarlarını barındırır.
- Eski sonuçlarla uyumluluk:
  - Payload çözümlenemediğinde tarihçe boş kabul edilir.
  - Eksik meta bilgisi varsa varsayılan boş meta kullanılır.

## 3. Arayüz Davranışı

- “Örüntüleme” açıkken her IOU analizinin sonunda iki panel görünür:
  1. **Klasik pattern paneli**: Mevcut yüklemenin sonuçlarını listeler (eski davranış).
  2. **Toplu örüntüler**: Önceki analizlerden devralınan pattern’larla zincirlenmiş kombinasyonları listeler. Varsayılan olarak kapalı `<details>` kartı içinde; alt grup başlıkları da ayrıca kapalı gelir.
- Toplu panelde her grup “`<başlangıç değeri> ile başlayanlar (adet)`” formatı ile listelenir.
- Pattern satırları numaralandırılır, devam ihtimalleri `(devam: …)` ifadesiyle gösterilir.

## 4. Kod Referansları

- `app72/web.py`
  - `render_combined_pattern_panel`, `build_chained_pattern_sequences`, `_apply_pattern_sequence`
  - `App72Handler.do_POST` içinde pattern meta/state yönetimi ve form hidden alanları.

## 5. Test

- Syntax kontrolü: `python3 -m compileall app72/web.py`
- Manuel doğrulama adımı:
  1. IOU sayfasında birkaç dosya grubu sırayla çalıştır.
  2. “Toplu örüntüler” panelini açarak grupların beklenen şekilde zincirlenip numaralandırıldığını kontrol et.
