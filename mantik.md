# IOU Pattern Zinciri Mantığı (app72 Referansı)

Bu doküman, `app72` IOU örüntü (pattern) zincirleme mantığını diğer uygulamalara taşırken dikkat edilmesi gereken noktaları özetler.

## 1. Amaç

- Birden fazla IOU çalıştırması yapıldığında, her çalışmanın pattern sonuçlarını tek bir “toplu” listede zincirlemek.
- Zincirleme sırasında pattern kurallarını (±1/±3 üçlüleri, ±2 başlangıçları, 0 dönüşleri vb.) bozmamak.
- Joker ve dosya adı bilgilerini koruyarak görsel tutarlılığı sağlamak.

## 2. Veri Saklama (Stacked State)

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
- İlk analizde `previous_pattern_payload` boş olur.
- Sonraki analizlerde payload decode edilir, mevcut pattern seti tarihçeye eklenir ve yeniden encode edilip forma geri yazılır.
- Payload çözümlenemiyorsa güvenli biçimde boş kabul edilmeli (zincir sıfırdan başlar).

Ömür döngüsü (lifecycle):
- Post(1): pattern üret → payload yok → sadece klasik panel.
- Post(2): pattern üret → eski payload + bu set → “Toplu örüntüler” çiz.
- Post(3+): aynı mantıkla genişleterek devam et.

## 3. Zincirleme Hesabı (Kural Koruma)

1. Pattern dizileri, durum makinesi (`_apply_pattern_sequence`) aracılığıyla tek tek doğrulanır.
2. `build_chained_pattern_sequences` fonksiyonu:
   - Pattern gruplarını (analiz sırası) alır.
   - Her olası devam yolunu kurala uygun şekilde seçer.
   - Kuralları ihlal eden birleştirmeler elenir (0–X–0 yasağı, ardışık aynı değer yasağı, ±1/±3 üçlüleri, ±2 başlatma, ilk adım ±1/±3 sonrası tek seferlik 0 istisnası vb.).
   - Değerler sadece ilgili setlerin ürettiği pattern dizilerinden gelir; XYZ’de olmayan yeni değer icat edilmez.
   - Beam/limit ayarı yoksa tüm geçerli dallar hesaplanır (performans değerlendirmesi yapın).
3. Sonuç, aynı mantıkla render edilen birleşik pattern dizileri döner.

## 4. UI Render (Görsellik)

- Klasik pattern paneli (tek analiz) `render_pattern_panel` çağrısıyla gösterilir.
- Zincirlenmiş sonuçlar `render_combined_pattern_panel` ile eklenir:
  - Olası pattern dizileri başlangıç değerine göre gruplanır; her grup `<details>` altında.
  - Sıralama, kombinasyonların oluşturulma sırasına göre korunur (ters sıralama yok).
  - Token görseli ve tooltip’ler için `render_pattern_panel` kullanıldığı için Joker/renk mantığı değişmez.

## 5. Taşıma Adımları (Öneri)

1. `app72/web.py` içindeki şu yardımcıları hedef uygulamaya taşı:
   - `_initial_pattern_state`, `_apply_pattern_sequence`, `_allowed_values_for_state`
   - `build_patterns_from_xyz_lists`, `build_chained_pattern_sequences`
   - `render_pattern_panel`, `render_combined_pattern_panel`
2. IOU POST akışında (pattern_mode seçiliyken):
   - Yüklenen her dosya için XYZ offset kümesini üret ve `all_xyz_sets` listesine koy.
   - Görsellik için `all_file_names` ve Joker işaretli indeksleri topla.
   - `current_patterns = build_patterns_from_xyz_lists(all_xyz_sets, allow_zero_after_start=True)`
   - `history_groups = decode(previous_pattern_payload.groups) + [current_patterns]`
   - `meta_history = decode(previous_pattern_payload.meta) + [{"file_names": all_file_names, "joker_indices": sorted(joker_indices)}]`
   - `combined_panel = render_combined_pattern_panel(history_groups, meta_history, allow_zero_after_start=True)`
   - Klasik panelin yanına/topuna `combined_panel` HTML’ini ekle.
   - Formu tekrar çizerken `previous_pattern_payload`’ı yeni history/meta ile base64-JSON olarak sakla.
3. Eski payload’larla uyumluluk için `try/except` ile decode et, tip kontrolleri yap, eksik alanları boş varsayımla doldur.
4. UI: klasik pattern paneli (tek analiz) + altında toplu örüntüler (gruplu `<details>`).
5. Test: ardışık 3+ analiz çalıştır, zincirde sıra ve kuralların korunduğunu; Joker/dosya adlarının tooltip’te göründüğünü doğrula.

## 6. Notlar (Performans ve Konfig)

- Beam ve sonuç sınırları app72’de limitsizdir (None). Diğer uygulamalarda gerekirse `PATTERN_BEAM_WIDTH` ve `PATTERN_MAX_PATHS` parametrelerini ekleyip sınırlayabilirsin.
- Pattern kurallarında farklılık varsa (`allow_zero_after_start` gibi), payload’a bu bayrakları ekle.
- Belge güncellemelerini unutma (ör. `agents.md` benzeri rehberlere kısa not ekle).

## 7. Kontrol Listesi (Checklist)

- [ ] Pattern kuralları bire bir uygulandı (0–X–0 yok, ardışık aynı değer yok, ±1/±3 üçlüleri, ±2 kuralı, tek seferlik 0 istisnası).
- [ ] Zincir sadece mevcut pattern dizilerinden oluşturuluyor; XYZ’de olmayan değer eklenmiyor.
- [ ] Sıralama doğal akışta; ters görünüm yok.
- [ ] Joker ve dosya adları tooltip’te korunuyor.
- [ ] Payload varsa decode/encode güvenli; yoksa güvenle baştan başlıyor.
- [ ] Büyük setlerde performans kabul edilebilir; gerekirse beam/limit devreye alınabilir.

## 8. Uygulama Örnekleri

- app72: İlk uygulama; tüm zincirleme akışı burada implemente edildi.
- app120: Aynı mantık 120m için taşındı; sınırsız beam/yol ve `Toplu örüntüler` paneli aktif.
