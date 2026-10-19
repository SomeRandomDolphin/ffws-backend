# Monitoring archive

Laravel remains at the backend repository root. FastAPI is restored from the migration branch into this separate service.

## Components

`Dockerfile.monitoring` installs monitoring-only dependencies, excluding MLflow, sklearn, XGBoost and CUDA. `PREDICTIONS_ENABLED=false` leaves prediction routes present but returns 503. Monitoring readiness depends only on the archive schema. To enable models later, build with `INSTALL_PREDICTIONS=true`, supply compatible artifacts and enable the flag.

Run `python -m monitoring.worker init` once before the API/worker. `ARCHIVE_DATABASE_URL` must point to a dedicated writable archive schema. `observations` stores original source records and UTC timestamps, `source_state` stores independent live/backfill cursors and errors, `import_batches` stores import reports, and `export_jobs` stores the durable export queue. No automatic observation deletion occurs. Only one worker instance is supported; export recovery on startup assumes a single worker.

Surabaya source credentials remain read-only in `configs/surabaya/database.local.yaml`. An initial tail makes current readings available immediately; separate bounded batches backfill up to 366 days before each source's last observation. `source_state.detail` reports `backfill_complete`, historical cursor and skipped rows. Cursor persistence, deduplication and independent source errors survive restarts. Backfill progress must finish before a full-year source history can be expected.

## Data historis Welang sementara

Dua file CSV sementara dengan header dan urutan stasiun yang sama disimpan di `imports/history/` dan dimuat ke arsip Welang oleh worker:

- `welang-2022.csv`: 1 Oktober–5 Desember 2022 (3.135 baris waktu)
- `welang-2023.csv`: 1 Januari–14 Maret 2023 (3.481 baris waktu)

Keduanya memakai kolom `Datetime` lalu 15 stasiun dengan nama kanonis. Data sumber XLSX memiliki satu kolom `Curah hujan` tanpa identitas pos yang jelas; kolom itu tidak dimasukkan. Nilai muka air disimpan sebagai cm mentah dan disajikan sebagai meter pada endpoint grafik. Timestamp tanpa offset dianggap WIB. Data ini belum diverifikasi dan tidak mengisi rentang waktu yang kosong.

File dimuat otomatis dari `/app/imports/history` saat worker berjalan. Impor manual menerima direktori atau satu file CSV/XLSX: `python -m monitoring.worker import-excel --path imports/history`. `Dockerfile.monitoring` menyertakan CSV tersebut di image. Pada stack gabungan, Compose memasang direktori host `services/monitoring-api/imports/history` ke `/app/imports/history`; isi checkout host itulah yang dibaca worker. Folder `source-originals` tidak dipasang.

## Excel dan sumber berikutnya

File tambahan dapat diletakkan di `/app/imports/history` atau diarahkan dengan `WELANG_IMPORT_PATH`. Script frontend `initialize-stack.ps1 -WelangExcelPath` menyalin Excel ke `imports/history/welang.xlsx`. File lama di `imports/welang.xlsx` tidak dibaca otomatis oleh konfigurasi default; salin ke `history` jika masih diperlukan. `WELANG_IMPORT_PATH` dapat menunjuk direktori atau satu file di dalam container; setelah mengubah environment, buat ulang worker dengan Compose `up -d`. Worker memeriksa CSV/XLSX/XLSM berdasarkan checksum dan mengimpor file yang berubah. CSV historis tersebut menjadi baseline sementara.

Time headers accept DateTime/Datetime/tanggal/Time. LP/LD map to Purwodadi/Dhompo water level in meters; RC/RL map to Cendono/Lawang rainfall in mm. Canonical station-name headers are accepted. Timestamps without a timezone are WIB. Nulls remain null; invalid rows are reported. Import retries and unchanged files are idempotent. Corrected files replace matching observations without deleting unrelated dates. Rainfall gauges are separate from water-level stations.

## Future Welang provider

Leave `WELANG_API_URL` blank until the provider supports this normalized contract or a vendor adapter is implemented. `WELANG_API_TOKEN` is an optional server-only Bearer token. Requests send `since=<previous next_cursor>`.

```json
{"records":[{"station":"welang-dhompo","sensor":"water_level","metric":"water_level","time":"2026-10-17T12:00:00+07:00","value":0.315,"unit":"m","id":"123"}],"next_cursor":"123"}
```

Return bounded pages with stable IDs within each station and an unchanged cursor when caught up. Water-level readings accept meters or centimeters; rainfall uses mm. Keep sensor identity `water_level` when replacing LP/LD so overlapping records can be resolved. Display prefers API over Excel at identical timestamp/sensor/metric; original source rows remain in raw exports. Display converts Welang centimeters to meters consistently; the archive retains original units. API failures preserve newer saved API readings and historical Excel. Data timestamps are never changed to the present.

## Routes

- `GET /health/live`, `/health/ready`, `/health`: process/archive readiness, independent of models.
- `GET /v1/capabilities`: preserved Laravel features, frontend usage, model/Telegram flags; no secrets.
- `GET /v1/stations?region=welang|surabaya`: all map stations including empty ones.
- `GET /v1/stations/{id}/snapshot`: latest observation per signal and source status. Query history separately for chart series.
- `GET /v1/stations/{id}/history?hours=6|24|168`, or ISO `from`/`to`: up to 31 days. Chart extrema are retained during downsampling.
- `POST /v1/stations/{id}/exports`: 202 job ID; 409 for an empty station, 429 when the queue is full.
- `GET /v1/exports/{id}` and `/file`: job status/download. Files expire after 24 hours.
- `GET /surabaya/snapshot`: compatibility endpoint; use `?include_history=false` for lightweight map readings. Full history remains the default for older consumers. Drawer charts fetch `/v1/stations/{station}/history?hours=24&sensor={sensor}` only when opened.
- `/predict`, `/predict-multistation`, `/model-info`: preserved, disabled by default.

Monthly Excel sheets cover 12 calendar months ending at the latest data month. Rows retain original timestamps, values, sensor, metric, unit and source identity. Empty months are labeled. Dates/numbers are typed and workbook generation streams to disk. Sheets exceeding Excel's row limit continue in another part for the same month. File generation never uses chart samples or inferred zero values.

Run monitoring checks using `python -m pytest monitoring_tests/test_archive.py` with monitoring requirements and pytest/httpx installed. Tests use temporary SQLite databases and generated fixtures. The original model-dependent suite remains for when compatible artifacts are available.
## Kalibrasi pengamatan

Acuan operasional Surabaya disimpan di `configs/surabaya/live_sources.yaml`: Hang Tuah/Kalibokor 500 cm, Pucang 530 cm, Menur 300 cm (dipulihkan dari commit frontend `0aaf983`). API menghitung `waterLevelCm = max(0, referenceCm - jarakCm)`; `null` tetap `null`. Acuan ini merupakan konfigurasi operasional, bukan klaim verifikasi lapangan.

Pada `/v1/stations/.../snapshot` dan `/history`, setiap series menyertakan `measurement` dan `referenceCm`, dan setiap point menyertakan `waterLevelCm` serta `capacityPercent` jika acuannya tersedia. `value`, `metric`, serta `unit` tetap menjelaskan pembacaan sumber. Endpoint kompatibilitas `/surabaya/snapshot` juga menyertakan acuan, tinggi air, dan persentase pada sensor, serta tinggi air pada riwayatnya. FE hanya mengubah satuan tampilan; arsip database dan ekspor Excel tetap memuat catatan asli. Perubahan acuan diterapkan saat membaca API tanpa mengubah arsip lama.

Artefak `.pkl` tidak dimasukkan dalam Git atau image monitoring. Untuk mengaktifkan prediksi di kemudian hari, sediakan artefak kompatibel melalui mount terpisah bersama dependensi model.
