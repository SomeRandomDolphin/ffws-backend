<?php

namespace App\Console\Commands;

use App\Models\NotifikasiTelegram;
use App\Models\StationWaterLevel;
use Exception;
use Illuminate\Console\Command;
use Illuminate\Support\Carbon;
use Illuminate\Support\Collection;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;
use RuntimeException;
use Telegram\Bot\Laravel\Facades\Telegram;
use Throwable;

class PredictEveryOneHour extends Command
{
    /**
     * Iterates over every daerah configured in config('app.predicted_daerah')
     * and runs a prediction for each against its own model endpoint. All
     * daerah share the same 24-row, 13-station history payload — only the
     * target endpoint and where the result is stored differ.
     *
     * Still assumes station_water_levels is kept up to date by something
     * else (CSV import for now); this command only reads it.
     */
    protected $signature = 'predict:everyonehour';
    protected $description = 'Run /predict for every configured daerah using the last 24 rows of station_water_levels.';

    private const MIN_HISTORY_ROWS = 24;

    public function handle(): int
    {
        try {
            $rows = $this->loadHistoryRows();
            if ($rows === null) {
                return 0; // reason already logged via warn()
            }

            $payload = $this->buildPredictPayload($rows);

            if ($this->payloadHasNulls($payload)) {
                $this->warn('Latest history contains missing station readings; /predict would reject this. Skipping until imputation/carry-forward is decided.');
                return 0;
            }

            $daerahConfigs = config('app.predicted_daerah', []);
            if (empty($daerahConfigs)) {
                $this->warn('No predicted_daerah configured in app.php; nothing to predict.');
                return 0;
            }

            $anyFailed = false;
            foreach ($daerahConfigs as $daerah => $endpoints) {
                $ok = $this->runPredictionFor($daerah, $endpoints, $payload);
                $anyFailed = $anyFailed || !$ok;
            }

            $this->info('Cron executed successfully!');
            return $anyFailed ? 1 : 0;
        } catch (Throwable $exception) {
            Log::error('predict:everyonehour failed: ' . $exception->getMessage(), ['exception' => $exception]);
            throw $exception;
        }
    }

    /**
     * Returns the last 24 rows (oldest first) if there are enough of them
     * and they're gap-free on 30-minute boundaries, otherwise logs why and
     * returns null so handle() can skip the run cleanly.
     */
    private function loadHistoryRows()
    {
        $rows = StationWaterLevel::orderBy('tanggal', 'desc')
            ->limit(self::MIN_HISTORY_ROWS)
            ->get()
            ->sortBy('tanggal')
            ->values();

        if ($rows->count() < self::MIN_HISTORY_ROWS) {
            $this->warn("Only {$rows->count()} rows available in station_water_levels; need at least " . self::MIN_HISTORY_ROWS . '. Skipping.');
            return null;
        }

        for ($i = 1; $i < $rows->count(); $i++) {
            $delta = Carbon::parse($rows[$i]->tanggal)->diffInSeconds(Carbon::parse($rows[$i - 1]->tanggal));
            if ($delta !== 1800) {
                $this->warn('Latest rows are not on continuous, gap-free 30-minute boundaries; skipping this run.');
                return null;
            }
        }

        return $rows;
    }

    private function buildPredictPayload(Collection $rows): array
    {
        $history = $rows->map(function (StationWaterLevel $row) {
            return [
                'timestamp' => Carbon::parse($row->tanggal)->format('Y-m-d\TH:i:s'),
                'readings' => $row->toReadings(),
            ];
        })->values()->all();

        return ['history' => $history];
    }

    private function payloadHasNulls(array $payload): bool
    {
        foreach ($payload['history'] as $row) {
            if (in_array(null, $row['readings'], true)) {
                return true;
            }
        }
        return false;
    }

    /**
     * Runs prediction for a single daerah: health check, /predict call,
     * store, alert. Returns false (and logs/warns) on any failure so the
     * caller can keep going with the remaining daerah instead of aborting
     * the whole run over one bad model.
     */
    private function runPredictionFor(string $daerah, array $endpoints, array $payload): bool
    {
        if (!$this->isModelReady($endpoints['health_url'] ?? null)) {
            $this->error("[{$daerah}] ML API is not ready (health check failed); skipping.");
            return false;
        }

        $response = Http::timeout(30)->post($endpoints['predict_url'], $payload);

        if ($response->status() === 422) {
            Log::error("[{$daerah}] /predict rejected the payload (422): " . $response->body());
            $this->error("[{$daerah}] Payload validation failed on the ML API side. See logs.");
            return false;
        }

        if (!$response->successful()) {
            Log::error("[{$daerah}] /predict call failed with status {$response->status()}: " . $response->body());
            $this->error("[{$daerah}] Prediction call failed with status {$response->status()}.");
            return false;
        }

        try {
            $stored = $this->storePrediction($daerah, $response->json());
        } catch (RuntimeException $e) {
            Log::error("[{$daerah}] Failed to store prediction: " . $e->getMessage());
            $this->error("[{$daerah}] " . $e->getMessage());
            return false;
        }

        $this->notifyIfDangerous($daerah, $stored);
        $this->info("[{$daerah}] Prediction executed successfully!");
        return true;
    }

    private function isModelReady(?string $healthUrl): bool
    {
        if (!$healthUrl) {
            Log::warning('No health_url configured for this daerah; assuming not ready.');
            return false;
        }

        try {
            return Http::timeout(10)->get($healthUrl)->status() === 200;
        } catch (Exception $e) {
            Log::warning("Health check failed for {$healthUrl}: " . $e->getMessage());
            return false;
        }
    }

    /**
     * Persists the /predict response for this daerah and returns the row
     * of values (including computed statuses) so notifyIfDangerous() can
     * reuse it without re-querying.
     */
    private function storePrediction(string $daerah, array $data): array
    {
        $predictions = $data['predictions'] ?? [];
        $shadow = $data['shadow_predictions'] ?? [];

        $sourceTimestamp = $this->toMysqlDatetime($data['timestamp'] ?? null);
        if ($sourceTimestamp === null) {
            Log::error("[{$daerah}] /predict response had no usable 'timestamp' field; cannot store this prediction. Raw value: " . json_encode($data['timestamp'] ?? null));
            throw new RuntimeException("[{$daerah}] Missing/unparseable timestamp in /predict response.");
        }

        $values = [
            'daerah' => $daerah,
            'source_timestamp' => $sourceTimestamp,
            'prediction_time' => $this->toMysqlDatetime($data['prediction_time'] ?? null) ?? now()->format('Y-m-d H:i:s'),
            'backend' => $data['backend'] ?? null,
            'serving_tier' => $data['serving_tier'] ?? null,
            'models' => isset($data['models']) ? json_encode($data['models']) : null,
            'degradation' => isset($data['degradation']) ? json_encode($data['degradation']) : null,
            'quality_flags' => isset($data['quality_flags']) ? json_encode($data['quality_flags']) : null,
            'h1' => $predictions['h1'] ?? null,
            'h2' => $predictions['h2'] ?? null,
            'h3' => $predictions['h3'] ?? null,
            'h4' => $predictions['h4'] ?? null,
            'h5' => $predictions['h5'] ?? null,
            'shadow_h1' => $shadow['h1'] ?? null,
            'shadow_h2' => $shadow['h2'] ?? null,
            'shadow_h3' => $shadow['h3'] ?? null,
            'shadow_h4' => $shadow['h4'] ?? null,
            'shadow_h5' => $shadow['h5'] ?? null,
        ];

        $threshold = DB::table('stasiun_air')->where('nama_pos', $daerah)->first();
        foreach (['h1', 'h2', 'h3', 'h4', 'h5'] as $h) {
            $values["status_{$h}"] = $threshold ? $this->classify($values[$h], $threshold) : null;
        }

        $existing = DB::table('station_predictions')
            ->where('daerah', $daerah)
            ->where('source_timestamp', $values['source_timestamp'])
            ->first();

        if ($existing) {
            DB::table('station_predictions')
                ->where('daerah', $daerah)
                ->where('source_timestamp', $values['source_timestamp'])
                ->update($values);
        } else {
            DB::table('station_predictions')->insert($values);
        }

        return $values;
    }

    /**
     * The ML API's timestamps aren't guaranteed to be MySQL-safe as-is —
     * `prediction_time` in particular has been observed as ISO 8601 with
     * fractional seconds and a trailing "Z"
     * (e.g. "2026-07-07T03:33:25.694332Z"), which MySQL's DATETIME
     * columns reject outright. Always normalize through Carbon before
     * inserting.
     */
    private function toMysqlDatetime(?string $value): ?string
    {
        if ($value === null || trim($value) === '') {
            return null;
        }

        try {
            return Carbon::parse($value)->format('Y-m-d H:i:s');
        } catch (Exception $e) {
            Log::warning("Could not parse timestamp '{$value}' from ML API response: " . $e->getMessage());
            return null;
        }
    }

    private function classify(?float $value, object $threshold): ?string
    {
        if ($value === null) {
            return null;
        }
        if ($value < $threshold->batas_air_siaga) {
            return 'AMAN';
        }
        if ($value < $threshold->batas_air_awas) {
            return 'SIAGA';
        }
        return 'BAHAYA';
    }

    private function notifyIfDangerous(string $daerah, array $values): void
    {
        $dangerMessages = [];
        foreach (['h1', 'h2', 'h3', 'h4', 'h5'] as $h) {
            if (($values["status_{$h}"] ?? null) === 'BAHAYA') {
                $dangerMessages[] = "(Status BAHAYA) Prediksi {$daerah} {$h}: {$values[$h]}";
            }
        }

        if (empty($dangerMessages)) {
            return;
        }

        $resultString = implode(PHP_EOL, $dangerMessages);
        $allChatIds = NotifikasiTelegram::all()->pluck('chat_id')->all();
        foreach ($allChatIds as $chatId) {
            Telegram::sendMessage([
                'chat_id' => $chatId,
                'text' => $resultString,
            ]);
        }
    }
}