<?php

namespace Database\Seeders;

use App\Models\StasiunAirPos;
use Illuminate\Database\Console\Seeds\WithoutModelEvents;
use Illuminate\Database\Seeder;

class StasiunAirPosSeeder extends Seeder
{
    /**
     * Run the database seeds.
     *
     * IMPORTANT: all thresholds below are PLACEHOLDERS, computed
     * mechanically as (observed_max + 20%/50% of observed_range) from the
     * min/max table shared for the new CSV data source — not real
     * hydrological siaga/awas levels. They exist so classify() in
     * PredictEveryOneHour resolves to something instead of null, and so
     * a fresh deployment doesn't immediately misfire BAHAYA on every
     * reading. Replace with real thresholds from whoever owns that
     * domain knowledge before trusting alerting in production.
     *
     * `nama_pos` uses the same snake_case keys as
     * StationWaterLevel::STATION_COLUMN_MAP / config('app.predicted_daerah')
     * / station_predictions.daerah, so any future daerah just needs one
     * consistent key added everywhere rather than a per-table naming
     * convention to reconcile.
     *
     * Only 'dhompo' has an active model today (config('app.predicted_daerah')).
     * The rest are seeded ahead of time so adding a new predicted daerah
     * later is a config change, not a missing-row bug.
     */
    public function run(): void
    {
        $data = [
            ['nama_pos' => 'bd_suwoto',       'batas_air_siaga' => 507.803, 'batas_air_awas' => 508.823],
            ['nama_pos' => 'krajan_timur',    'batas_air_siaga' => 339.642, 'batas_air_awas' => 340.666],
            ['nama_pos' => 'purwodadi',       'batas_air_siaga' => 296.028, 'batas_air_awas' => 298.269],
            ['nama_pos' => 'bd_baong',        'batas_air_siaga' => 207.874, 'batas_air_awas' => 217.582],
            ['nama_pos' => 'bd_lecari',       'batas_air_siaga' => 170.069, 'batas_air_awas' => 170.635],
            ['nama_pos' => 'bd_bakalan',      'batas_air_siaga' => 143.353, 'batas_air_awas' => 145.086],
            ['nama_pos' => 'awlr_kademungan', 'batas_air_siaga' => 155.005, 'batas_air_awas' => 161.640],
            ['nama_pos' => 'bd_domas',        'batas_air_siaga' => 68.319,  'batas_air_awas' => 71.077],
            ['nama_pos' => 'bd_guyangan',     'batas_air_siaga' => 34.888,  'batas_air_awas' => 35.460],
            ['nama_pos' => 'bd_grinting',     'batas_air_siaga' => 36.015,  'batas_air_awas' => 38.002],
            ['nama_pos' => 'sidogiri',        'batas_air_siaga' => 27.599,  'batas_air_awas' => 28.338],
            ['nama_pos' => 'klosod',          'batas_air_siaga' => 26.751,  'batas_air_awas' => 27.762],
            ['nama_pos' => 'dhompo',          'batas_air_siaga' => 16.782,  'batas_air_awas' => 19.203],
        ];

        StasiunAirPos::insert($data);
    }
}