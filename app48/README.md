# app48

app48, 48 dakikalık (48m) zaman diliminde çalışan, UTC-4 18:00 başlangıçlı sayım yapan uygulamadır. DC (Distorted Candle) kuralları, S1/S2 dizileri ve offset mantığı agents.md ile aynıdır. Bu app'in farkı, sayımın başladığı ilk gün hariç diğer günlerde piyasanın kapalı olduğu 18:00–19:36 aralığı için 18:00 ve 18:48 saatlerinde suni (sentetik) mumlar eklemesidir.

## Kuralların özeti
- Başlangıç: UTC-4 18:00 (sabit). Offset ile -3..+3 adım kaydırılabilir.
- Zaman dilimi: Girdi `UTC-4` veya `UTC-5`. Girdi `UTC-5` ise tüm zaman damgaları +1 saat kaydırılarak çıktılar `UTC-4`'e normalize edilir.
- 48m akışı: 18:00, 18:48, 19:36, 20:24, 21:12, ... şeklinde ilerler.
- Sentetik mum ekleme: İlk gün HARİÇ, her gün için 17:12 -> 19:36 arasına 18:00 ve 18:48 mumları eklenir (veride 17:12 ve 19:36 mevcutsa). OHLC değerleri 17:12 ile 19:36 arasındaki doğrusal geçişe göre üretilir (open=önceki close, close ara değeri; high/low=open/close'un min/max'ı).
- DC kuralı: DC mumlar sayımda atlanır (adım aralarında DC olmayan mumlar sayılır). Dizinin ilk değeri (ör. 1) başlangıç mumuna yazılır (DC olsa da).
- DC istisnası (app48): Pazartesi–Cumartesi 13:12 - 19:36 (19:36 dahil) aralığındaki mumlar DC sayılmaz; bu slotlar arasında kalan 18:00, 18:48 ve 19:36 mumları normal kabul edilir.
- IOU kuralı: 18:00, 18:48 ve 19:36 zaman damgalı mumlar IOU listesine girmez.
- Diziler: S1 ve S2 agents.md’de tanımlandığı gibidir.

## CLI Kullanımı
```
python3 -m app48.main \
  --csv path/to/data.csv \
  --input-tz UTC-5 \
  --sequence S2 \
  --offset 0 \
  --show-dc
```

- `--csv`: CSV dosya yolu (Time, Open, High, Low, Close (Last)).
- `--input-tz`: Girdi verisinin zaman dilimi: `UTC-4` veya `UTC-5` (varsayılan `UTC-5`). Çıktı daima `UTC-4`e normalize edilir.
- `--sequence`: `S1` veya `S2`.
- `--offset`: Başlangıç ofseti adım sayısı: `-3`..`+3`.
- `--show-dc`: DC bilgisi çıktı satırlarına eklenir.

## Örnek çıktı
```
Data: 832 candles (after synth), tf ~48m
Range: 2023-05-01 00:00:00 -> 2023-06-01 23:12:00
TZ: UTC-5 -> UTC-4 (+1h)
Start: base_idx=120 ts=2023-05-10 18:00:00 (aligned), offset=0 -> idx=120 ts=2023-05-10 18:00:00
Sequence: S2 [1, 5, 9, 17, ...]
1 -> idx=120 ts=2023-05-10 18:00:00 DC=False
5 -> idx=124 ts=2023-05-10 21:12:00 DC=False
9 -> idx=128 ts=2023-05-11 00:24:00 DC=True
...
```

## Web Arayüzü

Basit bir web arayüzü ile tarayıcıdan CSV yükleyip analiz yapabilirsiniz:

```
python3 -m app48.web --port 2020
```

Tarayıcıda `http://127.0.0.1:2020/` adresine gidin; CSV’yi seçip `Girdi TZ` (`UTC-4`/`UTC-5`), `Dizi`, `Offset` ve `DC Göster` seçeneklerini belirleyin. Sonuçlar HTML tablo olarak listelenir. Sentetik ekleme yapıldıysa sonuç kartında eklenen mum sayısı gösterilir.
