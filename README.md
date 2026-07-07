<p align="center"><a href="https://laravel.com" target="_blank"><img src="https://raw.githubusercontent.com/laravel/art/master/logo-lockup/5%20SVG/2%20CMYK/1%20Full%20Color/laravel-logolockup-cmyk-red.svg" width="400" alt="Laravel Logo"></a></p>

<p align="center">
<a href="https://github.com/laravel/framework/actions"><img src="https://github.com/laravel/framework/workflows/tests/badge.svg" alt="Build Status"></a>
<a href="https://packagist.org/packages/laravel/framework"><img src="https://img.shields.io/packagist/dt/laravel/framework" alt="Total Downloads"></a>
<a href="https://packagist.org/packages/laravel/framework"><img src="https://img.shields.io/packagist/v/laravel/framework" alt="Latest Stable Version"></a>
<a href="https://packagist.org/packages/laravel/framework"><img src="https://img.shields.io/packagist/l/laravel/framework" alt="License"></a>
</p>

## About Repository

Backend project Repository for Flood Forecasting Warning Sistem Dinas PU Sumber Daya Air Jatim <br>

As of the latest update, the ML prediction backend has moved from the old Flask/TensorFlow/Keras service to a FastAPI-based multi-horizon service (see [Tech Stack](#tech-stack-for-flood-forecasting-warning-system-web-monitoring) below and `MIGRATION_NOTICE.md` for what changed on the API surface consumed by the frontend).

## Tech Stack For Flood Forecasting Warning System Web Monitoring
| Web Development                                    | Artificial Intelligence                                  |
|------------------------------------------------------|-----------------------------------------------------------|
| [![Laravel][Laravel.com]][Laravel-url]               | [![Python][python.com]][python-url]                        |
| [![MySQL][mysql.com]][mysql-url]                     | [![PyTorch][PyTorch.com]][PyTorch-url]                     |
| [![React][React.com]][React-url]                     | [![scikit-learn][sklearn.com]][sklearn-url]                |
| [![TailwindCSS][TailwindCSS.com]][TailwindCSS-url]   | [![FastAPI][FastAPI.com]][FastAPI-url]                     |

> The ML service itself lives in a separate repository and is called by this backend's `predict:everyonehour` scheduled command — see the ML repo's own README for its `/predict`, `/health`, and `/model-info` contract.

## Requirements for This Back-End Repository
* [PHP 8.1](https://www.php.net)
* [MySQL](https://www.mysql.com)
* [Laravel 9](https://laravel.com)

## Getting Started
1. Clone this repository
```
git clone https://github.com/SomeRandomDolphin/ffws-backend.git
```
2. Install All Dependencies
```
composer install
```
3. Copy the .env.example to .env
```
cp .env.example .env
```
4. Generate API Key
```
php artisan key:generate
```
5. Generate JWT Secret
```
php artisan jwt:secret
```
6. Adjust your .env with your environment (see [Environment Variables](#environment-variables) below for the ML prediction keys specifically)
7. Run database migrations
```
php artisan migrate
```
8. Seed the database (roles, station thresholds, etc. — required before predictions can resolve AMAN/SIAGA/BAHAYA status)
```
php artisan db:seed
```
9. Start the application
```
php artisan serve
```
10. Open a new terminal, and start the scheduler (this is what runs `predict:everyonehour` and the Telegram notification poller on their configured intervals)
```
php artisan schedule:work
```

## Environment Variables

In addition to the standard Laravel `.env` keys, this project uses the following for ML prediction integration. Each predicted daerah gets its own pair — only `dhompo` is active today (see `config('app.predicted_daerah')` in `config/app.php`):

| Key | Purpose | Default |
|---|---|---|
| `DHOMPO_PREDICT_URL` | `/predict` endpoint for the Dhompo model | `https://www.its.ac.id/tsipil/informasibanjir/ffws-ml/predict` |
| `DHOMPO_HEALTH_URL` | `/health` endpoint checked before each prediction run | `https://www.its.ac.id/tsipil/informasibanjir/ffws-ml/health` |

To add a new predicted station later, add its own `{STATION}_PREDICT_URL` / `{STATION}_HEALTH_URL` pair and a corresponding entry in `config('app.predicted_daerah')` — no other code changes are required.

> The old `FLASK_URL` / `SIH3_*` keys (SIH-3 telemetry polling, single Flask endpoint) have been removed — they're no longer read anywhere in the codebase.

## Database Seeding Notes

`php artisan db:seed` runs, among others, `StasiunAirPosSeeder`, which populates `stasiun_air` with the water-level thresholds (`batas_air_siaga` / `batas_air_awas`) used to classify predictions into AMAN/SIAGA/BAHAYA status.

**The seeded values are placeholders**, mechanically derived from observed min/max ranges in available sample data — they are **not** validated hydrological thresholds. Replace them with real values from the relevant domain expert before relying on flood alerting in production.

If thresholds are updated *after* predictions already exist in `station_predictions`, run:
```
php artisan predict:recalculate-status
```
to backfill `status_h1`–`h5` on existing rows — `classify()` only runs once, at prediction-insert time, so existing rows won't pick up new thresholds automatically.

## Web Endpoint Documentation
https://documenter.getpostman.com/view/56404958/2sBY4Jw2uk

See also `MIGRATION_NOTICE.md` for a summary of breaking changes to `/getHistory`, `/getHistoryPrediction`, and `/getChartData` since the ML backend migration.

## License
The Laravel framework is open-sourced software licensed under the [MIT license](https://opensource.org/licenses/MIT).

[Laravel.com]: https://img.shields.io/badge/laravel-%23FF2D20.svg?style=for-the-badge&logo=laravel&logoColor=white
[Laravel-url]: https://laravel.com
[mysql.com]: https://img.shields.io/badge/mysql-%2300f.svg?style=for-the-badge&logo=mysql&logoColor=white
[mysql-url]: https://www.mysql.com
[python.com]: https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54
[python-url]: https://www.python.org
[PyTorch.com]: https://img.shields.io/badge/PyTorch-%23EE4C2C.svg?style=for-the-badge&logo=PyTorch&logoColor=white
[PyTorch-url]: https://pytorch.org
[sklearn.com]: https://img.shields.io/badge/scikit--learn-%23F7931E.svg?style=for-the-badge&logo=scikit-learn&logoColor=white
[sklearn-url]: https://scikit-learn.org
[FastAPI.com]: https://img.shields.io/badge/FastAPI-%23009688.svg?style=for-the-badge&logo=fastapi&logoColor=white
[FastAPI-url]: https://fastapi.tiangolo.com
[React.com]: https://img.shields.io/badge/react-%2320232a.svg?style=for-the-badge&logo=react&logoColor=%2361DAFB
[React-url]: https://react.dev
[TailwindCSS.com]: https://img.shields.io/badge/tailwindcss-%2338B2AC.svg?style=for-the-badge&logo=tailwind-css&logoColor=white
[TailwindCSS-url]: https://tailwindcss.com