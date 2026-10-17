<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Generalizes dhompo_predictions -> station_predictions now that each
     * daerah/station is expected to eventually get its own model, rather
     * than a single Dhompo-only model forever.
     */
    public function up(): void
    {
        Schema::rename('dhompo_predictions', 'station_predictions');

        Schema::table('station_predictions', function (Blueprint $table) {
            $table->string('daerah')->default('dhompo')->after('id');
        });

        // Existing rows all came from the Dhompo-only model; the column
        // default above already backfills them, this just makes it explicit.
        DB::table('station_predictions')->whereNull('daerah')->update(['daerah' => 'dhompo']);

        Schema::table('station_predictions', function (Blueprint $table) {
            // NOTE: renaming a table does not rename its indexes in MySQL,
            // so the old unique constraint is still named after
            // dhompo_predictions — must be dropped by its original name.
            $table->dropUnique('dhompo_predictions_source_timestamp_unique');
            $table->unique(['daerah', 'source_timestamp'], 'station_predictions_daerah_source_timestamp_unique');
        });
    }

    public function down(): void
    {
        Schema::table('station_predictions', function (Blueprint $table) {
            $table->dropUnique('station_predictions_daerah_source_timestamp_unique');
            $table->unique('source_timestamp', 'dhompo_predictions_source_timestamp_unique');
            $table->dropColumn('daerah');
        });

        Schema::rename('station_predictions', 'dhompo_predictions');
    }
};