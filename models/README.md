# Artefak Model

| Lokasi | Kegunaan |
|---|---|
| `sklearn/` | Model baseline lokal Dhompo |
| `pytorch/` | Model dari notebook deep learning Dhompo |
| `surabaya/urban/` | Model Surabaya dengan target `persistence_residual` pada konfigurasi default |
| `surabaya/urban_delta/` | Model varian target `delta` Surabaya |

Model adaptive tetap berada di `artifacts/tier_a_adaptive/`. Run yang dilatih melalui MLflow menyimpan model di artifact store MLflow, bukan otomatis pada folder di atas.

## Ketersediaan

Lima model baseline Dhompo yang sebelumnya sudah ada di repository tetap berada di lokasi lamanya. Model baru, scaler, checkpoint, dan metadata model Surabaya diabaikan Git. Clone repository membutuhkan artefak Surabaya lokal berupa model `.pkl`, `standard_scaler_global.pkl`, dan `training_metadata.json` dari run yang sama.

Saat penataan, folder sklearn Dhompo tidak memiliki `scaler.pkl`. Jangan menganggap keberhasilan memuat file model sudah menjamin kesesuaian fitur/skala untuk prediksi. Sediakan artefak yang cocok dari run asal sebelum menggunakan prediksi untuk integrasi.

Jangan mencampurkan model, scaler, dan metadata dari eksperimen berbeda. Nama file internal tetap dipertahankan. Path absolut historis pada metadata Surabaya didukung melalui pencarian nama file di direktori model terpilih apabila lokasi lama tidak tersedia.

Lihat [Dhompo](../docs/dhompo/index.md), [Surabaya](../docs/surabaya/index.md), dan [API](../docs/deployment/index.md) untuk cara training serta pemuatan model.
