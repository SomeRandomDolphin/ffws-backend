<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    /**
     * Replaces hasil_prediksi's role. The old table stored one row per
     * predicted_for_time across 6 model x station combinations
     * (purwodadi/dhompo x lstm/gru/tcn). The new /predict API returns one
     * multi-horizon (h1-h5) Dhompo-only prediction per call, plus new
     * metadata (serving_tier, degradation, shadow_predictions,
     * quality_flags) that hasil_prediksi has no columns for.
     */
    public function up(): void
    {
        Schema::create('dhompo_predictions', function (Blueprint $table) {
            $table->id();

            // Last observation timestamp from the request (data.timestamp).
            // One prediction run per distinct source timestamp.
            $table->timestamp('source_timestamp')->unique()->index();
            $table->timestamp('prediction_time')->nullable();

            $table->string('backend')->nullable();
            $table->string('serving_tier')->nullable();
            $table->json('models')->nullable();
            $table->json('degradation')->nullable();
            $table->json('quality_flags')->nullable();

            $table->decimal('h1', 10, 3)->nullable();
            $table->decimal('h2', 10, 3)->nullable();
            $table->decimal('h3', 10, 3)->nullable();
            $table->decimal('h4', 10, 3)->nullable();
            $table->decimal('h5', 10, 3)->nullable();

            $table->decimal('shadow_h1', 10, 3)->nullable();
            $table->decimal('shadow_h2', 10, 3)->nullable();
            $table->decimal('shadow_h3', 10, 3)->nullable();
            $table->decimal('shadow_h4', 10, 3)->nullable();
            $table->decimal('shadow_h5', 10, 3)->nullable();

            // Per-horizon AMAN/SIAGA/BAHAYA classification against
            // stasiun_air's Dhompo thresholds.
            $table->string('status_h1')->nullable();
            $table->string('status_h2')->nullable();
            $table->string('status_h3')->nullable();
            $table->string('status_h4')->nullable();
            $table->string('status_h5')->nullable();
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('dhompo_predictions');
    }
};