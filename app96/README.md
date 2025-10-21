# app96 – 96 Dakikalık Timeframe Paketi

app96, 96 dakikalık mumlar için temel sayım ve IOU tarama araçlarını tek pakette sunar. Uygulamaya özel DC veya IOU istisnaları henüz eklenmemiştir; yalnızca genel kurallar uygulanır.

## Neler Var?

- 96m sequence sayımı (`app96.counter`)  
- DC listesi ve offset matrisi web sekmeleri  
- IOU taraması (multi CSV + haber/XYZ filtresi)  
- 12m → 96m converter (CLI + web)  
- UTC-5 → UTC-4 saat kaydırma desteği

## Örnek Kullanımlar

### CLI

```bash
# Sequence sayımı
python3 -m app96.counter --csv data.csv --sequence S2 --offset +1 --show-dc

# Belirli dizi değerini tahmin et
python3 -m app96.counter --csv data.csv --predict 37

# 12m veriyi 96m'e dönüştür
python3 -m app96.main --csv candles_12m.csv --input-tz UTC-5 --output candles_96m.csv
```

### Web Arayüzü

```bash
python3 -m app96.web --host 0.0.0.0 --port 2196
```

Sekmeler:
1. **Analiz** – Sequence sayımı ve tahminler  
2. **DC List** – Tespit edilen DC mumları  
3. **Matrix** – Tüm offsetlerin tablo görünümü  
4. **12→96 Converter** – 12 dakikalık mumları tek seferde dönüştürür  
5. **IOU Tarama** – Limit + tolerans koşulunu sağlayan aynı işaretli OC/PrevOC çiftlerini listeler, XYZ haber filtresi isteğe bağlıdır

## CSV Beklentileri

- Zorunlu sütunlar: `Time`, `Open`, `High`, `Low`, `Close (Last)` (eş anlamlılar desteklenir)  
- Veri timestamp’e göre sıralanır; `UTC-5` seçildiyse otomatik +1 saat uygulanır.  
- DC kuralı: 18:00 mumları DC değildir, ardışık DC engellenir. Diğer istisnalar daha sonra eklenecektir.

