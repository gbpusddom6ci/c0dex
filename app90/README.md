# app90 – 90 Dakikalık Timeframe Paketi

app90, 90 dakikalık mumlar için temel sequence sayımı, IOU taraması ve 30m → 90m dönüştürücüyü tek pakette toplar. Uygulama özelinde aşağıdaki slot istisnaları aktiftir: 18:00, (Pazar hariç) 19:30 ve Cuma 16:30 mumları DC değildir ve IOU listelerine alınmaz. IOU özelinde **15:00** ve **16:40** mumları da her gün sonuçlardan çıkarılır.

## Neler Var?

- 90m sequence sayımı (`app90.counter`)  
- DC listesi ve offset matrisi web sekmeleri  
- IOU taraması (multi CSV + haber/XYZ filtresi)  
- 30m → 90m converter (CLI + web)  
- UTC-5 → UTC-4 saat kaydırma desteği

## Örnek Kullanımlar

### CLI

```bash
# Sequence sayımı
python3 -m app90.counter --csv data.csv --sequence S2 --offset +1 --show-dc

# Belirli dizi değerini tahmin et
python3 -m app90.counter --csv data.csv --predict 37

# 30m veriyi 90m'e dönüştür
python3 -m app90.main --csv candles_30m.csv --input-tz UTC-5 --output candles_90m.csv
```

### Web Arayüzü

```bash
python3 -m app90.web --host 0.0.0.0 --port 2190
```

Sekmeler:
1. **Analiz** – Sequence sayımı ve tahminler  
2. **DC List** – Tespit edilen DC mumları  
3. **Matrix** – Tüm offsetlerin tablo görünümü  
4. **30→90 Converter** – 30 dakikalık mumları tek seferde dönüştürür  
5. **IOU Tarama** – Limit + tolerans koşulunu sağlayan aynı işaretli OC/PrevOC çiftlerini listeler, XYZ haber filtresi isteğe bağlıdır

## CSV Beklentileri

- Zorunlu sütunlar: `Time`, `Open`, `High`, `Low`, `Close (Last)` (eş anlamlılar desteklenir)  
- Veri timestamp’e göre sıralanır; `UTC-5` seçildiyse otomatik +1 saat uygulanır.  
- DC kuralı: 18:00, (Pazar hariç) 19:30 ve Cuma 16:30 mumları DC değildir; ardışık DC engellenir. IOU taramasında bu slotlara ek olarak 15:00 ve 16:40 mumları her gün dışlanır.
