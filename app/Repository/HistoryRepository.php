<?php

namespace App\Repository;

use App\Models\StationWaterLevel;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use InvalidArgumentException;

class HistoryRepository
{
    private const CHART_WINDOW = 24;

    /** A single /predict run only ever produces h1..h5 (5 hours ahead). */
    private const MAX_HORIZON = 5;

    public function getHistory($offsetReq, $limitReq, $daerah): array
    {
        try {
            $column = $this->resolveStationColumn($daerah);
        } catch (InvalidArgumentException $e) {
            // Matches the original behavior for an unrecognized daerah: an
            // empty result rather than an error. Rain daerah (lawang,
            // cendono) now land here too, since station_water_levels has
            // no rainfall columns — rain support was intentionally
            // dropped when /predict stopped needing it.
            return [
                'history' => null,
                'total_count' => 0,
            ];
        }

        $query = StationWaterLevel::select('id', $column, 'tanggal')
            ->orderBy('id', 'desc')
            ->offset($offsetReq)
            ->limit($limitReq);

        $data = $query->get();
        $totalCount = $query->toBase()->getCountForPagination();

        return [
            'history' => $data,
            'total_count' => $totalCount,
        ];
    }

    public function getHistoryPrediction($offset, $limit, $daerah = null): array
    {
        $commonTableExpression = DB::table('station_predictions')
            ->orderBy('station_predictions.id', 'desc');

        if ($daerah !== null && trim($daerah) !== '') {
            $commonTableExpression->where('daerah', strtolower(trim($daerah)));
        }

        $data = clone $commonTableExpression;
        $data = $data->offset($offset)->limit($limit)->get()->map(function ($row) {
            return [
                'id' => $row->id,
                'daerah' => $row->daerah,
                'source_timestamp' => $row->source_timestamp,
                'prediction_time' => $row->prediction_time,
                'backend' => $row->backend,
                'serving_tier' => $row->serving_tier,
                'models' => $row->models ? json_decode($row->models, true) : (object) [],
                'predictions' => [
                    'h1' => isset($row->h1) ? (float) $row->h1 : null,
                    'h2' => isset($row->h2) ? (float) $row->h2 : null,
                    'h3' => isset($row->h3) ? (float) $row->h3 : null,
                    'h4' => isset($row->h4) ? (float) $row->h4 : null,
                    'h5' => isset($row->h5) ? (float) $row->h5 : null,
                ],
                'shadow_predictions' => [
                    'h1' => isset($row->shadow_h1) ? (float) $row->shadow_h1 : null,
                    'h2' => isset($row->shadow_h2) ? (float) $row->shadow_h2 : null,
                    'h3' => isset($row->shadow_h3) ? (float) $row->shadow_h3 : null,
                    'h4' => isset($row->shadow_h4) ? (float) $row->shadow_h4 : null,
                    'h5' => isset($row->shadow_h5) ? (float) $row->shadow_h5 : null,
                ],
                'status' => [
                    'h1' => $row->status_h1, 'h2' => $row->status_h2, 'h3' => $row->status_h3,
                    'h4' => $row->status_h4, 'h5' => $row->status_h5,
                ],
                'degradation' => $row->degradation ? json_decode($row->degradation, true) : (object) [],
                'quality_flags' => $row->quality_flags ? json_decode($row->quality_flags, true) : (object) [],
            ];
        });

        $totalCount = clone $commonTableExpression;
        $totalCount = $totalCount->count();

        return [
            'history' => $data,
            'total_count' => $totalCount,
        ];
    }

    /**
     * Rebuilds the old rolling 24-slot actual+prediction chart on top of
     * station_predictions/station_water_levels.
     *
     * Behavior differences from the original, both forced by the new data
     * shape (one run = one source_timestamp -> h1..h5, not an arbitrary
     * number of independently-targeted predicted_for_time rows):
     *  - `model` is gone; the ML API picks one model per horizon.
     *  - `periode` (future hours to show) is capped at 5 — there's no
     *    longer a way to show further out than a single run's horizon.
     *  - Past "prediksi" values use each prior run's h1 (its nearest,
     *    1-hour-ahead forecast) as the best available estimate for that
     *    historical hour, mirroring the old chart's intent of comparing
     *    actual vs. the nearest-term prediction.
     */
    public function getChartHistory($daerah, $periode): array
    {
        $daerah = $daerah !== null ? strtolower(trim($daerah)) : 'dhompo';

        $predictedDaerah = config('app.predicted_daerah', []);
        if (!array_key_exists($daerah, $predictedDaerah)) {
            // 404: CustomExceptionHandler reads getCode() directly to set
            // the HTTP status, so this must be set explicitly. This is
            // stricter than the original, which had no such validation
            // and would instead fail with a raw SQL error on an unknown
            // daerah/model combination.
            throw new InvalidArgumentException(
                "Prediction chart is not yet available for '{$daerah}'. Supported: "
                . implode(', ', array_keys($predictedDaerah)) . '.',
                404
            );
        }

        $actualCol = $this->resolveStationColumn($daerah);

        $periode = $periode !== null ? (int) $periode : 1;
        $periode = max(0, min($periode, self::MAX_HORIZON));

        $latestActual = StationWaterLevel::select('id', $actualCol, 'tanggal')
            ->orderBy('tanggal', 'desc')
            ->limit(1)
            ->get();

        if ($latestActual->isEmpty()) {
            return [];
        }
        $latestActualTime = $latestActual[0]->tanggal;

        // Future points: from the most recent run at or before the latest
        // actual reading. h{i} targets source_timestamp + i hours.
        $latestRun = DB::table('station_predictions')
            ->where('daerah', $daerah)
            ->where('source_timestamp', '<=', $latestActualTime)
            ->orderBy('source_timestamp', 'desc')
            ->first();

        $futurePoints = collect();
        if ($latestRun && $periode > 0) {
            $runTime = Carbon::parse($latestRun->source_timestamp);
            for ($i = 1; $i <= $periode; $i++) {
                $value = $latestRun->{"h{$i}"};
                $futurePoints->push([
                    'aktual' => null,
                    'prediksi' => $value !== null ? (float) $value : null,
                    'tanggal' => $runTime->copy()->addHours($i)->format('Y-m-d H:i:s'),
                ]);
            }
        }

        // Past window: actual readings, each matched against the h1
        // (nearest-horizon) prediction from the run made 1 hour earlier.
        $pastCount = max(0, self::CHART_WINDOW - $periode);

        $pastActual = StationWaterLevel::select('id', $actualCol, 'tanggal')
            ->where('tanggal', '<=', $latestActualTime)
            ->orderBy('tanggal', 'desc')
            ->limit($pastCount)
            ->get()
            ->reverse()
            ->values();

        $pastPoints = $pastActual->map(function ($row) use ($actualCol, $daerah) {
            $priorRunTime = Carbon::parse($row->tanggal)->subHour()->format('Y-m-d H:i:s');

            $priorRun = DB::table('station_predictions')
                ->where('daerah', $daerah)
                ->where('source_timestamp', $priorRunTime)
                ->first();

            return [
                'aktual' => $row->{$actualCol},
                'prediksi' => isset($priorRun->h1) ? (float) $priorRun->h1 : null,
                'tanggal' => $row->tanggal,
            ];
        });

        return $pastPoints->concat($futurePoints)->values()->toArray();
    }

    /**
     * Normalizes a `daerah` value ("Bd. Suwoto", "bd-suwoto", "BD SUWOTO")
     * to the snake_case column name used in station_water_levels /
     * StationWaterLevel::STATION_COLUMN_MAP.
     *
     * @throws InvalidArgumentException if daerah is missing or isn't one
     *         of the 13 canonical stations.
     */
    private function resolveStationColumn(?string $daerah): string
    {
        if ($daerah === null || trim($daerah) === '') {
            throw new InvalidArgumentException('daerah is required.', 400);
        }

        $normalized = strtolower(trim($daerah));
        $normalized = str_replace(['.', '-', ' '], ['', '_', '_'], $normalized);
        $normalized = preg_replace('/_+/', '_', $normalized);
        $normalized = trim($normalized, '_');

        if (!array_key_exists($normalized, StationWaterLevel::STATION_COLUMN_MAP)) {
            throw new InvalidArgumentException("Daerah air tidak ditemukan: '{$daerah}'.", 404);
        }

        return $normalized;
    }
}