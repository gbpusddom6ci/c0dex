# IOU Pattern Zinciri Mantığı (app72 Referansı)

Bu doküman, `app72` IOU örüntü (pattern) zincirleme mantığını diğer uygulamalara taşırken dikkat edilmesi gereken noktaları özetler.

## 1. Amaç

- Birden fazla IOU çalıştırması yapıldığında, her çalışmanın pattern sonuçlarını tek bir “toplu” listede zincirlemek.
- Zincirleme sırasında pattern kurallarını (±1/±3 üçlüleri, ±2 başlangıçları, 0 dönüşleri vb.) bozmamak.
- Joker ve dosya adı bilgilerini koruyarak görsel tutarlılığı sağlamak.

## 2. Veri Saklama

- Formda iki önemli hidden alan kullanılır:
  - `previous_results_html`: Stacked analiz HTML içerikleri.
  - `previous_pattern_payload`: Base64 kodlu JSON.
- JSON yapısı:
  ```json
  {
    "groups": [  // önceki analizlerdeki pattern listeleri
      [[...], [...]],  // her iç liste bir pattern (offset sırası)
      ...
    ],
    "meta": [   // aynı sıradaki pattern listelerine ait metadata
      {
        "file_names": ["dosya1.csv", "dosya2.csv", ...],
        "joker_indices": [0, 2]  // dosya bazlı Joker seçimleri
      },
      ...
    ],
    "allow_zero_after_start": true
  }
  ```
- Payload çözümlenemiyorsa güvenli biçimde boş kabul etmeli.

## 3. Zincirleme Hesabı

1. Pattern dizileri, durum makinesi (`_apply_pattern_sequence`) aracılığıyla tek tek doğrulanır.
2. `build_chained_pattern_sequences` fonksiyonu:
   - Pattern gruplarını (analiz sırası) alır.
   - Her olası devam yolunu kurala uygun şekilde seçer.
   - Beam + limit parametreleri (`PATTERN_BEAM_WIDTH`, `PATTERN_MAX_PATHS`) ile sınırlar.
3. Sonuç, aynı mantıkla render edilen birleşik pattern dizileri döner.

## 4. UI Render

- Klasik pattern paneli (tek analiz) `render_pattern_panel` çağrısıyla gösterilir.
- Zincirlenmiş sonuçlar `render_combined_pattern_panel` ile eklenir:
  - Olası pattern dizileri başlangıç değerine göre gruplanır.
  - Her grup `<details>` altında tutulur; sıralama, kombinasyonların oluşturulma sırasına göre korunur.
  - Token görseli ve tooltip’ler için `render_pattern_panel` kullanıldığı için Joker/renk mantığı değişmez.

## 5. Taşıma Adımları (Öneri)

1. `app72/web.py` içindeki yardımcı fonksiyonları (durum makinesi + render helper’lar) hedef uygulamaya kopyala.
2. IOU POST akışında pattern meta/state saklama ve form hidden alanlarını ekle.
3. Eski payload’larla uyumlu olacak şekilde hata yakalama blokları ekle.
4. UI içinde klasik pattern panelinden sonra toplu paneli göster.
5. Manual test: ardışık birkaç tarama yap, zincir panelinin sıra ve içerik açısından doğru olduğundan emin ol.

## 6. Notlar

- Eğer beam veya max paths değerleri uygulamalar arası değişiklik gerektiriyorsa konfigüre edilebilir hale getir.
- Pattern kurallarında farklılık varsa (`allow_zero_after_start` gibi), payload’a bu bayrakları ekle.
- Belge güncellemelerini unutma (ör. `agents.md` benzeri rehberlere kısa not ekle).
