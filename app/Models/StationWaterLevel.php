<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Factories\HasFactory;
use Illuminate\Database\Eloquent\Model;

class StationWaterLevel extends Model
{
    use HasFactory;

    protected $table = 'station_water_levels';
    public $incrementing = true;
    public $timestamps = false;

    protected $fillable = [
        'tanggal',
        'bd_suwoto',
        'krajan_timur',
        'purwodadi',
        'bd_lecari',
        'bd_bakalan',
        'bd_baong',
        'awlr_kademungan',
        'bd_guyangan',
        'sidogiri',
        'bd_domas',
        'klosod',
        'bd_grinting',
        'dhompo',
    ];

    /**
     * Without this, MySQL decimal columns come back from PDO as strings
     * (e.g. "9.145" instead of 9.145), which is exactly what was showing
     * up in getHistory/getChartData responses.
     */
    protected $casts = [
        'bd_suwoto'       => 'float',
        'krajan_timur'    => 'float',
        'purwodadi'       => 'float',
        'bd_lecari'       => 'float',
        'bd_bakalan'      => 'float',
        'bd_baong'        => 'float',
        'awlr_kademungan' => 'float',
        'bd_guyangan'     => 'float',
        'sidogiri'        => 'float',
        'bd_domas'        => 'float',
        'klosod'          => 'float',
        'bd_grinting'     => 'float',
        'dhompo'          => 'float',
    ];

    /**
     * Raw source header name (as found in CSV/TSV/Excel exports) => DB
     * column. Shared by ImportStationReadings (CSV/TSV) and
     * ExcelImportController (.xlsx), so the mapping only needs updating
     * in one place.
     *
     * "Bd. Sentono" and "Jalan Nasional" are intentionally NOT mapped:
     * they are dropped on import (not required by the /predict ML API).
     */
    public const HEADER_COLUMN_MAP = [
        'Bd. Suwoto'      => 'bd_suwoto',
        'Krajan Timur'    => 'krajan_timur',
        'Purwodadi'       => 'purwodadi',
        'Bd. Lecari'      => 'bd_lecari',
        'Bd. Bakalan'     => 'bd_bakalan',
        'Bd. Baong'       => 'bd_baong',
        'AWLR Kademungan' => 'awlr_kademungan',
        'Bd Guyangan'     => 'bd_guyangan',
        'Sidogiri'        => 'sidogiri',
        'Bd. Domas'       => 'bd_domas',
        'Klosod'          => 'klosod',
        'Bd. Grinting'    => 'bd_grinting',
        'Dhompo'          => 'dhompo',
    ];

    /**
     * DB column => canonical station name required by the ML API's
     * /predict `readings` object. Keep these values EXACT (including
     * periods and spacing) — the API matches station names verbatim.
     *
     * Used by toReadings() below (column -> canonical name, for building
     * the /predict request payload).
     */
    public const STATION_COLUMN_MAP = [
        'bd_suwoto'       => 'Bd. Suwoto',
        'krajan_timur'    => 'Krajan Timur',
        'purwodadi'       => 'Purwodadi',
        'bd_lecari'       => 'Bd. Lecari',
        'bd_bakalan'      => 'Bd. Bakalan',
        'bd_baong'        => 'Bd. Baong',
        'awlr_kademungan' => 'AWLR Kademungan',
        'bd_guyangan'     => 'Bd Guyangan',
        'sidogiri'        => 'Sidogiri',
        'bd_domas'        => 'Bd. Domas',
        'klosod'          => 'Klosod',
        'bd_grinting'     => 'Bd. Grinting',
        'dhompo'          => 'Dhompo',
    ];

    /**
     * Build the `readings` object for this row in the exact shape
     * /predict's HistoryRow expects: {"Bd. Suwoto": 1.23, ...}.
     *
     * Note: the /predict schema currently rejects nulls for any station
     * (see README). If any column here is null, this row is NOT yet
     * safe to send as-is — that gap still needs a decision (impute,
     * carry-forward, or reject the request).
     */
    public function toReadings(): array
    {
        $readings = [];
        foreach (self::STATION_COLUMN_MAP as $column => $canonicalName) {
            $readings[$canonicalName] = $this->{$column} !== null
                ? (float) $this->{$column}
                : null;
        }
        return $readings;
    }
}