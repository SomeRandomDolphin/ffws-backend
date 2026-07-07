<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Run the migrations.
     *
     * Replaces awlr_arr_per_jam's role as the sensor history source, but
     * scoped to water level only, at 30-minute resolution, across all 13
     * stations required by the ML API's /predict endpoint.
     *
     * Column names are snake_case for DB safety; the canonical station
     * names required by /predict (e.g. "Bd. Suwoto", "AWLR Kademungan")
     * are mapped in App\Models\StationWaterLevel::STATION_COLUMN_MAP.
     */
    public function up(): void
    {
        Schema::create('station_water_levels', function (Blueprint $table) {
            $table->id();
            $table->timestamp('tanggal')->unique()->index();

            $table->decimal('bd_suwoto', 10, 3)->nullable();
            $table->decimal('krajan_timur', 10, 3)->nullable();
            $table->decimal('purwodadi', 10, 3)->nullable();
            $table->decimal('bd_lecari', 10, 3)->nullable();
            $table->decimal('bd_bakalan', 10, 3)->nullable();
            $table->decimal('bd_baong', 10, 3)->nullable();
            $table->decimal('awlr_kademungan', 10, 3)->nullable();
            $table->decimal('bd_guyangan', 10, 3)->nullable();
            $table->decimal('sidogiri', 10, 3)->nullable();
            $table->decimal('bd_domas', 10, 3)->nullable();
            $table->decimal('klosod', 10, 3)->nullable();
            $table->decimal('bd_grinting', 10, 3)->nullable();
            $table->decimal('dhompo', 10, 3)->nullable();
        });
    }

    /**
     * Reverse the migrations.
     */
    public function down(): void
    {
        Schema::dropIfExists('station_water_levels');
    }
};