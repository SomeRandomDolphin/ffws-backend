<?php

namespace App\Console\Commands;

use App\Models\StationWaterLevel;
use Exception;
use Illuminate\Console\Command;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;
use RuntimeException;
use Throwable;

class ImportStationReadings extends Command
{
    /**
     * php artisan import:station-readings storage/app/imports/batch.csv
     * php artisan import:station-readings storage/app/imports/batch.tsv --delimiter="\t"
     */
    protected $signature = 'import:station-readings
                            {path : Path to the CSV/TSV file}
                            {--delimiter= : Field delimiter; auto-detected from the header row if omitted}';

    protected $description = 'Import a station water level CSV/TSV export into station_water_levels';

    /**
     * @deprecated Use StationWaterLevel::HEADER_COLUMN_MAP directly.
     *             Kept as an alias only so this class's own references
     *             below don't need touching.
     */
    private const HEADER_COLUMN_MAP = StationWaterLevel::HEADER_COLUMN_MAP;

    public function handle(): int
    {
        $path = $this->argument('path');

        if (!is_readable($path)) {
            $this->error("File not found or not readable: {$path}");
            return 1;
        }

        $handle = fopen($path, 'r');
        if ($handle === false) {
            $this->error("Could not open file: {$path}");
            return 1;
        }

        $headerLine = fgets($handle);
        $delimiter = $this->option('delimiter') ?: $this->detectDelimiter($headerLine);

        $headers = array_map('trim', str_getcsv(trim($headerLine), $delimiter));

        $datetimeIndex = array_search('Datetime', $headers, true);
        if ($datetimeIndex === false) {
            $this->error("Could not find a 'Datetime' column in the header row.");
            fclose($handle);
            return 1;
        }

        // Map each recognized header to its column index. Anything not in
        // HEADER_COLUMN_MAP (e.g. Bd. Sentono, Jalan Nasional, or a typo)
        // is silently skipped rather than imported.
        $columnIndexes = [];
        foreach ($headers as $index => $header) {
            if (isset(self::HEADER_COLUMN_MAP[$header])) {
                $columnIndexes[self::HEADER_COLUMN_MAP[$header]] = $index;
            }
        }

        $missingStations = array_diff(array_keys(self::HEADER_COLUMN_MAP), $headers);
        if (!empty($missingStations)) {
            $this->warn('Header row is missing expected station(s): ' . implode(', ', $missingStations));
        }

        $imported = 0;
        $updated = 0;
        $skipped = 0;
        $rowNumber = 1; // header was row 1

        DB::beginTransaction();
        try {
            while (($line = fgets($handle)) !== false) {
                $rowNumber++;
                $line = rtrim($line, "\r\n");
                if ($line === '') {
                    continue;
                }

                $row = str_getcsv($line, $delimiter);
                $rawTimestamp = trim($row[$datetimeIndex] ?? '');

                if ($rawTimestamp === '') {
                    $this->warn("Row {$rowNumber}: empty timestamp, skipped.");
                    $skipped++;
                    continue;
                }

                try {
                    $timestamp = Carbon::parse($rawTimestamp);
                } catch (Exception $e) {
                    $this->warn("Row {$rowNumber}: unparseable timestamp '{$rawTimestamp}', skipped.");
                    $skipped++;
                    continue;
                }

                if (!in_array($timestamp->minute, [0, 30], true) || $timestamp->second !== 0) {
                    $this->warn("Row {$rowNumber}: timestamp {$timestamp} is not on a 30-minute boundary, skipped.");
                    $skipped++;
                    continue;
                }

                $values = ['tanggal' => $timestamp->format('Y-m-d H:i:s')];
                foreach ($columnIndexes as $column => $index) {
                    $raw = trim($row[$index] ?? '');
                    $values[$column] = $raw === '' ? null : (float) str_replace(',', '.', $raw);
                }

                $existing = StationWaterLevel::where('tanggal', $values['tanggal'])->first();
                if ($existing) {
                    $existing->fill($values)->save();
                    $updated++;
                } else {
                    StationWaterLevel::create($values);
                    $imported++;
                }
            }

            DB::commit();
        } catch (Throwable $e) {
            DB::rollBack();
            fclose($handle);
            Log::error('Station readings import failed: ' . $e->getMessage(), ['exception' => $e]);
            throw new RuntimeException('Import failed: ' . $e->getMessage(), 0, $e);
        }

        fclose($handle);

        $this->info("Done. Inserted: {$imported}, Updated: {$updated}, Skipped: {$skipped}.");
        return 0;
    }

    private function detectDelimiter(string $headerLine): string
    {
        return substr_count($headerLine, "\t") >= substr_count($headerLine, ',') ? "\t" : ',';
    }
}