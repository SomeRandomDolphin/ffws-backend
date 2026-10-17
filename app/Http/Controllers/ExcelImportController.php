<?php

namespace App\Http\Controllers;

use App\Http\Response\Response;
use App\Models\StationWaterLevel;
use Exception;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;
use Maatwebsite\Excel\Facades\Excel;
use PhpOffice\PhpSpreadsheet\Shared\Date;
use RuntimeException;

class ExcelImportController extends Controller
{
    /**
     * Imports a station water level Excel export (Datetime + up to 13
     * canonical stations, extra columns like "Bd. Sentono" / "Jalan
     * Nasional" dropped) into station_water_levels.
     *
     * Replaces the old import (tanggal/RC/RL/LP/LD -> awlr_arr_per_jam),
     * which only handled 2 water-level + 2 rain columns.
     */
    public function importExcel(Request $request): JsonResponse
    {
        try {
            $file = $request->file('file');
            $sheet = Excel::toCollection([], $file)[0]->toArray();

            $columnHeaders = array_map('trim', $sheet[0]);

            $datetimeIndex = array_search('Datetime', $columnHeaders);
            if ($datetimeIndex === false) {
                throw new RuntimeException("Could not find a 'Datetime' column in the header row.", 400);
            }

            // Map each recognized header to its column index. Anything not
            // in StationWaterLevel::HEADER_COLUMN_MAP (e.g. Bd. Sentono,
            // Jalan Nasional, or a typo) is silently skipped rather than
            // imported.
            $columnIndexes = [];
            foreach ($columnHeaders as $index => $header) {
                if (isset(StationWaterLevel::HEADER_COLUMN_MAP[$header])) {
                    $columnIndexes[StationWaterLevel::HEADER_COLUMN_MAP[$header]] = $index;
                }
            }

            $missingStations = array_diff(
                array_values(StationWaterLevel::HEADER_COLUMN_MAP),
                array_intersect(array_values(StationWaterLevel::HEADER_COLUMN_MAP), array_keys($columnIndexes))
            );

            $imported = 0;
            $updated = 0;
            $skipped = 0;
            $skippedReasons = [];

            DB::beginTransaction();

            foreach (array_slice($sheet, 1) as $rowNumber => $row) {
                $displayRow = $rowNumber + 2; // +1 for 0-index, +1 for header row

                $rawTimestamp = $row[$datetimeIndex] ?? null;
                if ($rawTimestamp === null || trim((string) $rawTimestamp) === '') {
                    $skipped++;
                    $skippedReasons[] = "Row {$displayRow}: empty timestamp.";
                    continue;
                }

                try {
                    // Excel dates come through as serial numbers; plain
                    // datetime strings (as in this export) parse directly.
                    $timestamp = is_numeric($rawTimestamp)
                        ? Carbon::instance(Date::excelToDateTimeObject($rawTimestamp))
                        : Carbon::parse((string) $rawTimestamp);
                } catch (Exception $e) {
                    $skipped++;
                    $skippedReasons[] = "Row {$displayRow}: unparseable timestamp '{$rawTimestamp}'.";
                    continue;
                }

                if (!in_array($timestamp->minute, [0, 30], true) || $timestamp->second !== 0) {
                    $skipped++;
                    $skippedReasons[] = "Row {$displayRow}: timestamp {$timestamp} is not on a 30-minute boundary.";
                    continue;
                }

                $values = ['tanggal' => $timestamp->format('Y-m-d H:i:s')];
                foreach ($columnIndexes as $column => $index) {
                    $raw = $row[$index] ?? null;
                    $raw = $raw !== null ? trim((string) $raw) : '';
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

            $summary = [
                'inserted' => $imported,
                'updated' => $updated,
                'skipped' => $skipped,
                'skipped_reasons' => $skippedReasons,
                'missing_stations_in_header' => array_values($missingStations),
            ];

            return response()->json(Response::success($summary, 'Data inserted successfully', 200));
        } catch (Exception $e) {
            if (DB::transactionLevel() > 0) {
                DB::rollBack();
            }
            Log::error('Error: ' . $e->getMessage() . ' caused by: ' . ($e->getPrevious() ? $e->getPrevious()->getMessage() : 'No previous exception'), ['exception' => $e]);
            throw new RuntimeException($e->getMessage() . ' caused by: ' . $e->getPrevious(), $e->getCode(), $e);
        }
    }
}