<?php

namespace App\Console;

use App\Console\Commands\PredictEveryOneHour;
use Illuminate\Console\Scheduling\Schedule;
use Illuminate\Foundation\Console\Kernel as ConsoleKernel;

class Kernel extends ConsoleKernel
{
    /**
     * The Artisan commands provided by your application.
     *
     * @var array
     */
    protected $commands = [
        PredictEveryOneHour::class,
    ];

    /**
     * Define the application's command schedule.
     */
    protected function schedule(Schedule $schedule)
    {
        if (config('monitoring.predictions_enabled')) {
            $schedule->command('predict:everyonehour')->hourlyAt(1);
        }
        if (config('monitoring.telegram_enabled')) {
            $schedule->command('polling:notificationtelegram')->everyFiveMinutes();
        }
    }

    /**
     * Register the commands for the application.
     *
     * @return void
     */
    protected function commands()
    {
        $this->load(__DIR__.'/Commands');

        require base_path('routes/console.php');
    }
}
